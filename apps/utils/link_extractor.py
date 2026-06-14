import re
from urllib.parse import urlparse

from django.conf import settings
from urlextract import URLExtract

from apps.ballot.models import Ballot
from apps.constitution.models import Section
from apps.broadcast.models import Broadcast
from apps.petition.models import Petition
from apps.posts.models import Post
from apps.survey.models import Survey


def extract_linked_object(text: str):
    extractor = URLExtract()
    urls = extractor.find_urls(text)

    domains = settings.ALLOWED_HOSTS

    matching_links = []

    for domain in domains:
        constitution_pattern = fr'{domain}/constitution\?(?:[^&\s]*&)*id=(\d+)'
        constitution_matches = re.findall(constitution_pattern, text)
        if len(constitution_matches) > 0:
            return Section.objects.get(id=constitution_matches[0])

        matching_links.extend(url for url in urls if domain in url)

    for link in matching_links:
        parsed_url = urlparse(link)
        integer_strings = re.findall(r'\d+', parsed_url.path)
        if len(integer_strings) > 0:
            if 'post' in parsed_url.path:
                return Post.objects.get(id=integer_strings[0])
            if 'meeting' in parsed_url.path or 'live-stream' in parsed_url.path:
                return Broadcast.objects.get(id=integer_strings[0])
            if 'ballot' in parsed_url.path:
                return Ballot.objects.get(id=integer_strings[0])
            if 'survey' in parsed_url.path:
                return Survey.objects.get(id=integer_strings[0])
            if 'petition' in parsed_url.path:
                return Petition.objects.get(id=integer_strings[0])
    return None
