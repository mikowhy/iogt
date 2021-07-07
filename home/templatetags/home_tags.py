from django import template

from home.models import FooterPage, SectionIndexPage
from iogt.settings.base import LANGUAGES

register = template.Library()


@register.inclusion_tag('home/tags/footer.html', takes_context=True)
def footer(context):
    return {
        'footer_pages': FooterPage.objects.live(),
        'request': context['request'],
    }


@register.inclusion_tag('home/tags/top_level_sections.html', takes_context=True)
def top_level_sections(context):
    section_index_page = SectionIndexPage.objects.first()
    return {
        'top_level_sections': section_index_page.get_children() if section_index_page else None,
        'request': context['request'],
    }


@register.inclusion_tag('home/tags/banners_list.html')
def render_banners_list(banners):
    return {'banners': banners}


@register.inclusion_tag('home/tags/articles_list.html')
def render_articles_list(articles):
    return {'articles': articles}


@register.inclusion_tag('home/tags/featured_content_list.html')
def render_featured_content_list(featured_content):
    return {'featured_content_items': featured_content}


@register.inclusion_tag('home/tags/sub_sections.html')
def render_sub_sections_list(sub_sections):
    return {'sub_sections': sub_sections}


@register.inclusion_tag('home/tags/polls.html')
def render_polls_list(polls):
    return {'polls': polls}


@register.inclusion_tag('home/tags/questionnaire.html')
def render_questionnaire_list(questionnaire):
    return {'questionnaire': questionnaire}


@register.inclusion_tag('home/tags/section_progress.html')
def render_user_progress(user_progress):
    return user_progress


@register.inclusion_tag('home/tags/sub_sections.html')
def render_sub_sections_list(sub_sections):
    return {'sub_sections': sub_sections}


@register.simple_tag
def locale_set(locale, url):
    for item in LANGUAGES:
        code = item[0]
        url = url.replace(f"/{code}/", "")
    return f'/{locale}/{url}'