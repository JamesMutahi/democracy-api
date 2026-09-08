from django.contrib.postgres.indexes import GinIndex, OpClass
from django.contrib.postgres.search import SearchVector, SearchVectorField
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F
from django.db.models.signals import post_delete, post_save

CONSTITUTION_CACHE_VERSION_KEY = "constitution:cache-version"


class Section(models.Model):
    numeral = models.CharField(max_length=5, blank=True, default='')
    text = models.TextField()
    is_title = models.BooleanField(default=False)

    parent = models.ForeignKey("self",
                               on_delete=models.CASCADE,
                               null=True,
                               blank=True,
                               related_name="subsections",
                               )
    search_vector = SearchVectorField(null=True, blank=True)

    class Meta:
        db_table = "Section"
        ordering = ["id"]
        verbose_name = "section"
        verbose_name_plural = "sections"
        indexes = [
            models.Index(fields=["parent_id"]),
            # Full-text search index
            GinIndex(fields=['search_vector'], name='section_search_vector_idx'),
            # Trigram indexes for fuzzy matching
            GinIndex(OpClass(F('text'), name='gin_trgm_ops'), name='section_text_trgm_idx'),
            GinIndex(OpClass(F('numeral'), name='gin_trgm_ops'), name='section_numeral_trgm_idx'),
        ]
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(parent=models.F("id")),
                name="section_parent_not_self",
            ),
        ]

    def __str__(self) -> str:
        label = f"{self.numeral} {self.text}".strip()
        return label[:100] or f"Section {self.pk}"

    def clean(self):
        super().clean()

        if self.parent_id and self.parent_id == self.pk:
            raise ValidationError(
                {"parent": "A section cannot be its own parent."}
            )

        if self.pk and self.parent_id:
            seen = {self.pk}

            try:
                ancestor = self.parent
            except Section.DoesNotExist:
                return

            while ancestor is not None:
                if ancestor.pk in seen:
                    raise ValidationError(
                        {"parent": "This parent would create a circular hierarchy."}
                    )

                seen.add(ancestor.pk)

                try:
                    ancestor = ancestor.parent
                except Section.DoesNotExist:
                    break

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        # Update search vector after save
        Section.objects.filter(pk=self.pk).update(
            search_vector=SearchVector('numeral', 'text', config='english'),
        )

def _bump_constitution_cache_version(sender, **kwargs):
    """
    Invalidate cached constitution list responses by bumping the cache version.

    The cached list keys include this version, so changing it makes old cached
    responses unreachable without needing cache pattern deletion.
    """
    version = cache.get(CONSTITUTION_CACHE_VERSION_KEY, 1)
    cache.set(CONSTITUTION_CACHE_VERSION_KEY, version + 1, timeout=None)


post_save.connect(_bump_constitution_cache_version, sender=Section)
post_delete.connect(_bump_constitution_cache_version, sender=Section)
