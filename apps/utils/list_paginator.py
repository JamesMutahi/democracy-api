from django.core.paginator import Paginator, PageNotAnInteger, EmptyPage
from django.db.models import QuerySet


def list_paginator(queryset: QuerySet, page: int, page_size: int):
    paginator = Paginator(queryset, page_size)
    try:
        page_obj = paginator.page(page)
    except PageNotAnInteger:
        page_obj = paginator.page(1)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)
    return page_obj
