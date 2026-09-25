import random
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.ballot.models import Ballot, BallotVote, Option, Reason


OPTION_DATA = [
    {
        "text": "Improve water supply",
        "weight": 45,
        "reasons": [
            "We need clean water in our area.",
            "Maji ni tatizo kubwa hapa.",
            "Water points are too far from homes.",
            "Tunahitaji visima na mabomba ya maji.",
            "Children walk long distances to fetch water.",
            "Maji haitoki kwa wiki nzima.",
            "Fix the broken water kiosk near the market.",
            "Serikali iweke maji safi kwa ward hii.",
            "Water bills are high but supply is unreliable.",
            "Clean water will reduce diseases.",
            "There is no clean drinking water in our village.",
            "Maji ya bomba yanakuwa usiku pekee.",
        ],
    },
    {
        "text": "Repair roads and drainage",
        "weight": 35,
        "reasons": [
            "Roads become impassable during rain.",
            "Barabara zimeharibika sana.",
            "We need drainage to stop flooding.",
            "Boda riders struggle with muddy roads.",
            "Please grade the feeder roads.",
            "Tunahitaji barabara nzuri kwa shule na hospitali.",
            "Traffic is bad because roads are narrow.",
            "Road maintenance should be regular.",
            "Dust is affecting businesses and homes.",
            "Better roads will improve transport of goods.",
            "Flooding destroys houses every rainy season.",
            "Barabara ya sokoni ni hatari kwa watoto.",
        ],
    },
    {
        "text": "Upgrade markets and trade",
        "weight": 20,
        "reasons": [
            "Market stalls are too few.",
            "Soko linahitaji usafi na usalama.",
            "Traders need shade and toilets.",
            "Please support small businesses.",
            "Tunahitaji soko la kisasa.",
            "Market fees are too high.",
            "Better markets will create jobs.",
            "Vendors need storage and cold rooms.",
            "Youth need spaces for business.",
            "Soko la sasa ni crowded and disorganized.",
            "We need a clean market for food vendors.",
            "Mama mboga wanahitaji mabehewa na usalama.",
        ],
    },
]

FOLLOW_UPS = [
    "This is urgent.",
    "Please consider this.",
    "Ni muhimu sana.",
    "It affects many families.",
    "Tunaomba msaada.",
    "This has been a problem for years.",
    "Inaathiri watoto na wazee.",
    "We were promised action before.",
]

PII_SNIPPETS = [
    " My phone is 0712 345 678.",
    " Call me on +254 712 345 678.",
    " Email test.user@example.com.",
    " My ID number is 1234567.",
    " M-Pesa account 1234567890.",
    " Paybill 123456.",
    " My account no: 01001234567890.",
    " Simu yangu ni 0700 111 222.",
]


class Command(BaseCommand):
    help = "Creates a test ballot with sample votes and reasons for development/testing."

    def add_arguments(self, parser):
        parser.add_argument(
            "--title",
            type=str,
            default="Test Ballot: County Priority",
        )
        parser.add_argument(
            "--users",
            type=int,
            default=120,
            help="Number of test users/voters to create or reuse.",
        )
        parser.add_argument(
            "--reason-rate",
            type=float,
            default=0.8,
            help="Probability that a voter also submits a reason.",
        )
        parser.add_argument(
            "--pii-rate",
            type=float,
            default=0.25,
            help="Probability that a reason includes fake PII.",
        )
        parser.add_argument(
            "--ended",
            action="store_true",
            help="Make the ballot already ended.",
        )
        parser.add_argument(
            "--summarize",
            action="store_true",
            help="Run summarization synchronously after generating data.",
        )
        parser.add_argument(
            "--queue-summary",
            action="store_true",
            help="Queue Celery summarization task after generating data.",
        )
        parser.add_argument(
            "--seed",
            type=int,
            default=42,
            help="Random seed for reproducible sample data.",
        )

    def handle(self, *args, **opts):
        random.seed(opts["seed"])

        User = get_user_model()
        now = timezone.now()

        if opts["ended"]:
            start_time = now - timedelta(days=2)
            end_time = now - timedelta(hours=1)
        else:
            start_time = now - timedelta(hours=1)
            end_time = now + timedelta(days=1)

        with transaction.atomic():
            ballot, created = Ballot.objects.update_or_create(
                title=opts["title"],
                defaults={
                    "description": (
                        "Test ballot generated for local development. "
                        "Options, votes, and reasons are fake."
                    ),
                    "county": None,
                    "constituency": None,
                    "ward": None,
                    "start_time": start_time,
                    "end_time": end_time,
                    "is_active": True,
                },
            )

            ballot_options = []

            for index, data in enumerate(OPTION_DATA, start=1):
                option, _ = Option.objects.update_or_create(
                    ballot=ballot,
                    text=data["text"],
                    defaults={"number": index},
                )
                ballot_options.append((option, data))

            # Remove old generated responses for this test ballot.
            Reason.objects.filter(ballot=ballot).delete()
            BallotVote.objects.filter(ballot=ballot).delete()

            votes = []
            reasons = []

            for i in range(opts["users"]):
                user = self._get_or_create_user(User, i)

                option, option_data = self._pick_option(ballot_options)

                votes.append(
                    BallotVote(
                        user=user,
                        ballot=ballot,
                        option=option,
                        voted_at=now - timedelta(minutes=random.randint(5, 2000)),
                    )
                )

                if random.random() <= opts["reason_rate"]:
                    text = self._make_reason(option_data, opts["pii_rate"])

                    reason_defaults = {"text": text}

                    # Compatible with newer Reason model that stores option.
                    if hasattr(Reason, "option"):
                        reason_defaults["option"] = option

                    # Compatible with newer Reason model that stores PII fields.
                    if hasattr(Reason, "redacted_text"):
                        reason_defaults.update(
                            {
                                "redacted_text": "",
                                "pii_entities": [],
                                "pii_redacted_at": None,
                            }
                        )

                    reasons.append(
                        Reason(
                            user=user,
                            ballot=ballot,
                            **reason_defaults,
                        )
                    )

            BallotVote.objects.bulk_create(votes, ignore_conflicts=True)
            Reason.objects.bulk_create(reasons, ignore_conflicts=True)

        self.stdout.write(
            self.style.SUCCESS(
                f"Created/updated ballot id={ballot.id} title='{ballot.title}'"
            )
        )
        self.stdout.write(f"Votes: {len(votes)}")
        self.stdout.write(f"Reasons: {len(reasons)}")
        self.stdout.write(f"Ballot ended: {opts['ended']}")
        self.stdout.write("Test user password: TestPass123!")

        if opts["summarize"]:
            self._summarize_sync(ballot)

        if opts["queue_summary"]:
            self._summarize_async(ballot)

    def _get_or_create_user(self, UserModel, index: int):
        """
        Creates test users in a way that usually works with custom user models.

        If your custom user model requires extra required fields, adjust this
        method.
        """

        username_field = getattr(UserModel, "USERNAME_FIELD", "username")
        base_username = f"test_user_{index}"
        name = f"Test User {index}"
        password = "Kenya123"

        if username_field == "email":
            lookup = {"email": f"{base_username}@example.com"}
        else:
            lookup = {username_field: base_username}

        user = UserModel.objects.filter(**lookup).first()

        if user:
            return user

        fields = dict(lookup)

        if hasattr(UserModel, "name") and "name" not in fields:
            fields["name"] = name

        if hasattr(UserModel, "email") and "email" not in fields:
            fields["email"] = f"{base_username}@example.com"

        try:
            user = UserModel.objects.create_user(
                password=password,
                **fields,
            )
        except TypeError:
            user = UserModel(**fields)
            user.set_password(password)
            user.save()

        return user

    def _pick_option(self, ballot_options):
        total_weight = sum(data["weight"] for _, data in ballot_options)
        roll = random.randint(1, total_weight)

        current = 0

        for option, data in ballot_options:
            current += data["weight"]

            if roll <= current:
                return option, data

        return ballot_options[0]

    def _make_reason(self, option_data, pii_rate: float) -> str:
        text = random.choice(option_data["reasons"])

        if random.random() < 0.2:
            text = f"{text} {random.choice(FOLLOW_UPS)}"

        if random.random() < pii_rate:
            text = f"{text}{random.choice(PII_SNIPPETS)}"

        return text

    def _summarize_sync(self, ballot: Ballot):
        try:
            from apps.ballot.services.summarizer import summarize_ballot_reasons
        except ImportError:
            self.stdout.write(
                self.style.WARNING(
                    "Could not import apps.ballot.services.summarizer. "
                    "Skipping synchronous summarization."
                )
            )
            return

        self.stdout.write("Running synchronous summarization...")

        result = summarize_ballot_reasons(ballot.pk)

        self.stdout.write(self.style.WARNING("Summary result:"))
        self.stdout.write(result.get("summary", ""))
        self.stdout.write(f"Themes: {len(result.get('themes', []))}")
        self.stdout.write(f"Method: {result.get('method')}")
        self.stdout.write(
            f"Reasons processed: {result.get('reasons_processed')}/"
            f"{result.get('reasons_total')}"
        )

    def _summarize_async(self, ballot: Ballot):
        try:
            from apps.ballot.tasks import summarize_ballot
        except ImportError:
            self.stdout.write(
                self.style.WARNING(
                    "Could not import apps.ballot.tasks.summarize_ballot. "
                    "Skipping queued summarization."
                )
            )
            return

        summarize_ballot.delay(ballot.pk)

        self.stdout.write(
            self.style.SUCCESS(
                f"Queued summarization task for ballot id={ballot.id}"
            )
        )