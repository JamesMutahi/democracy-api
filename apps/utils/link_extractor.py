import re
from urllib.parse import urlparse

from django.contrib.sites.models import Site
from urlextract import URLExtract

from apps.ballot.models import Ballot
from apps.constitution.models import Section
from apps.meeting.models import Meeting
from apps.petition.models import Petition
from apps.posts.models import Post
from apps.survey.models import Survey


def extract_linked_object(text: str):
    extractor = URLExtract()
    urls = extractor.find_urls(text)

    current_domain = Site.objects.get_current().domain

    matching_links = [url for url in urls if current_domain in url]

    for link in matching_links:
        parsed_url = urlparse(link)
        integer_strings = re.findall(r'\d+', parsed_url.path)
        if len(integer_strings) > 0:
            if 'post' in parsed_url.path:
                return Post.objects.get(id=integer_strings[0])
            if 'meeting' in parsed_url.path:
                return Meeting.objects.get(id=integer_strings[0])
            if 'ballot' in parsed_url.path:
                return Ballot.objects.get(id=integer_strings[0])
            if 'survey' in parsed_url.path:
                return Survey.objects.get(id=integer_strings[0])
            if 'petition' in parsed_url.path:
                return Petition.objects.get(id=integer_strings[0])
            if 'section' in parsed_url.path:
                return Section.objects.get(id=integer_strings[0])
    return None
