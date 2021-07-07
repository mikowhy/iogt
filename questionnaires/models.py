import json

from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.utils.functional import cached_property

from django.core.paginator import EmptyPage, PageNotAnInteger
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models
from django.shortcuts import render
from django.utils.translation import gettext_lazy as _
from home.blocks import MediaBlock
from home.mixins import PageUtilsMixin
from iogt_users.models import User
from modelcluster.fields import ParentalKey
from wagtail.admin.edit_handlers import (FieldPanel, InlinePanel,
                                         MultiFieldPanel, StreamFieldPanel)
from wagtail.contrib.forms.edit_handlers import FormSubmissionsPanel
from wagtail.contrib.forms.models import (AbstractForm, AbstractFormField,
                                          AbstractFormSubmission)
from wagtail.core import blocks
from wagtail.core.fields import StreamField
from wagtail.core.models import Page
from wagtail.images.blocks import ImageChooserBlock

from questionnaires.blocks import SkipLogicField, SkipState
from questionnaires.forms import SurveyForm, QuizForm
from questionnaires.utils import SkipLogicPaginator


class QuestionnairePage(Page, PageUtilsMixin):
    template = None
    parent_page_types = []
    subpage_types = []

    description = StreamField(
        [
            ("paragraph", blocks.RichTextBlock()),
            ("image", ImageChooserBlock()),
        ],
        null=True,
        blank=True,
    )
    thank_you_text = StreamField(
        [
            ("paragraph", blocks.RichTextBlock()),
            ("media", MediaBlock(icon="media")),
            ("image", ImageChooserBlock()),
        ],
        null=True,
        blank=True,
    )
    allow_anonymous_submissions = models.BooleanField(
        default=True,
        help_text=_(
            "Check this to allow users who are NOT logged in to complete surveys."
        ),
    )

    allow_multiple_submissions = models.BooleanField(
        default=True,
        help_text=_("Check this to allow multiple form submissions for users"),
    )
    submit_button_text = models.CharField(
        max_length=40, null=True, default="Submit", help_text=_("Submit button text")
    )

    direct_display = models.BooleanField(default=False)

    settings_panels = Page.settings_panels + [
        FieldPanel('direct_display')
    ]

    def __str__(self):
        return self.title

    def serve(self, request, *args, **kwargs):
        if (
            not self.allow_multiple_submissions
            and self.get_submission_class()
            .objects.filter(page=self, user__pk=request.user.pk)
            .exists()
        ):
            return render(request, self.template, self.get_context(request))
        if self.multi_step:
            return self.serve_questions_separately(request)

        return super().serve(request, *args, **kwargs)

    def serve_questions_separately(self, request, *args, **kwargs):
        session_key_data = "form_data-%s" % self.pk
        is_last_step = False
        step_number = request.GET.get("p", 1)

        if step_number == 1:
            request.session.pop(session_key_data, None)

        form_data = request.session.get(session_key_data, {})

        paginator = SkipLogicPaginator(
            self.get_form_fields(),
            request.POST,
            form_data,
        )

        try:
            step = paginator.page(step_number)
        except PageNotAnInteger:
            step = paginator.page(1)
        except EmptyPage:
            step = paginator.page(paginator.num_pages)
            is_last_step = True

        if request.method == "POST":
            # The first step will be submitted with step_number == 2,
            # so we need to get a form from previous step
            # Edge case - submission of the last step
            try:
                prev_step = (
                    step if is_last_step else paginator.page(step.previous_page_number())
                )
            except EmptyPage:
                prev_step = step
                step = paginator.page(prev_step.next_page_number())

            # Create a form only for submitted step
            prev_form_class = self.get_form_class_for_step(prev_step)
            prev_form = prev_form_class(
                paginator.new_answers,
                page=self,
                user=request.user
            )
            if prev_form.is_valid():
                # If data for step is valid, update the session
                form_data = request.session.get(session_key_data, {})
                form_data.update(prev_form.cleaned_data)
                request.session[session_key_data] = form_data

                if prev_step.has_next():
                    # Create a new form for a following step, if the following step is present
                    form_class = self.get_form_class_for_step(step)
                    form = form_class(page=self, user=request.user)
                else:
                    # If there is no next step, create form for all fields
                    form = self.get_form(
                        request.session[session_key_data],
                        page=self, user=request.user
                    )

                    if form.is_valid():
                        # Perform validation again for whole form.
                        # After successful validation, save data into DB,
                        # and remove from the session.
                        form_submission = self.process_form_submission(form)
                        request.session.pop(session_key_data, None)
                        # render the landing page
                        return self.render_landing_page(
                            request, form_submission, *args, **kwargs
                        )
            else:
                # If data for step is invalid
                # we will need to display form again with errors,
                # so restore previous state.
                form = prev_form
                step = prev_step
        else:
            # Create empty form for non-POST requests
            form_class = self.get_form_class_for_step(step)
            form = form_class(page=self, user=request.user)

        context = self.get_context(request)
        context["form"] = form
        context["fields_step"] = step
        return render(request, self.template, context)

    class Meta:
        abstract = True


class SurveyFormField(AbstractFormField):
    page = ParentalKey("Survey", on_delete=models.CASCADE, related_name="survey_form_fields")
    required = models.BooleanField(verbose_name=_('required'), default=False)
    admin_label = models.CharField(
        verbose_name=_('admin_label'),
        max_length=256,
        help_text=_('Column header used during CSV export of survey '
                    'responses.'),
    )
    skip_logic = SkipLogicField()
    page_break = models.BooleanField(
        default=False,
        help_text=_(
            'Inserts a page break which puts the next question onto a new page'
        )
    )
    panels = [
        FieldPanel('label'),
        FieldPanel('help_text'),
        FieldPanel('required'),
        FieldPanel('field_type', classname="formbuilder-type"),
        StreamFieldPanel('skip_logic'),
        FieldPanel('default_value', classname="formbuilder-default"),
        FieldPanel('admin_label'),
        FieldPanel('page_break'),
    ]

    @property
    def has_skipping(self):
        return any(
            logic.value['skip_logic'] != SkipState.NEXT
            for logic in self.skip_logic
        )

    def choice_index(self, choice):
        if choice:
            if self.field_type == 'checkbox':
                # clean checkboxes have True/False
                try:
                    return ['on', 'off'].index(choice)
                except ValueError:
                    return [True, False].index(choice)
            return self.choices.split(',').index(choice)
        else:
            return False

    def next_action(self, choice):
        return self.skip_logic[self.choice_index(choice)].value['skip_logic']

    def is_next_action(self, choice, *actions):
        if self.has_skipping:
            return self.next_action(choice) in actions
        return False

    def next_page(self, choice):
        logic = self.skip_logic[self.choice_index(choice)]
        return logic.value[self.next_action(choice)]


class Survey(QuestionnairePage, AbstractForm):
    base_form_class = SurveyForm

    parent_page_types = ["home.HomePage", "home.Section", "home.Article"]
    template = "survey/survey.html"
    multi_step = models.BooleanField(
        default=False,
        verbose_name="Multi-step",
        help_text="Whether to display the survey questions to the user one at"
        " a time, instead of all at once.",
    )

    content_panels = Page.content_panels + [
        FormSubmissionsPanel(),
        MultiFieldPanel(
            [
                FieldPanel("allow_anonymous_submissions"),
                FieldPanel("allow_multiple_submissions"),
                FieldPanel("submit_button_text"),
                FieldPanel("multi_step"),
            ],
            heading=_(
                "General settings for survey",
            ),
        ),
        MultiFieldPanel(
            [
                StreamFieldPanel("description"),
            ],
            heading=_(
                "Description at survey page",
            ),
        ),
        MultiFieldPanel(
            [
                StreamFieldPanel("thank_you_text"),
            ],
            heading="Description at thank you page",
        ),
        InlinePanel("survey_form_fields", label="Form fields"),
    ]

    @cached_property
    def has_page_breaks(self):
        return any(
            field.page_break
            for field in self.get_form_fields()
        )

    def get_form_class_for_step(self, step):
        return self.form_builder(step.object_list).get_form_class()

    def get_form_fields(self):
        return self.survey_form_fields.all()

    def get_submission_class(self):
        return UserSubmission

    def process_form_submission(self, form):
        from home.models import SiteSettings
        user = form.user
        self.get_submission_class().objects.create(
            form_data=json.dumps(form.cleaned_data, cls=DjangoJSONEncoder),
            page=self,
            user=user,
        )

        site_settings = SiteSettings.get_for_default_site()
        if site_settings.registration_survey and site_settings.registration_survey.pk == self.pk:
            user.has_filled_registration_survey = True
            user.save(update_fields=['has_filled_registration_survey'])

    def get_context(self, request, *args, **kwargs):
        context = super().get_context(request, *args, **kwargs)
        context.update({'back_url': request.GET.get('back_url')})
        return context

    def get_data_fields(self):
        data_fields = [
            ('user', _('User')),
            ('submit_time', _('Submission Date')),
            ('page_url', _('Page URL'))
        ]
        data_fields += [
            (field.clean_name, field.admin_label)
            for field in self.get_form_fields()
        ]
        return data_fields

    class Meta:
        verbose_name = "survey"
        verbose_name_plural = "surveys"


class UserSubmission(AbstractFormSubmission):
    user = models.ForeignKey(
        get_user_model(), on_delete=models.CASCADE, blank=True, null=True
    )

    def get_data(self):
        form_data = super().get_data()
        form_data.update(
            {
                "user": self.user if self.user else None,
                "page_url": self.page.full_url,
            }
        )
        return form_data


class PollFormField(AbstractFormField):
    page = ParentalKey("Poll", on_delete=models.CASCADE, related_name="poll_form_fields")
    CHOICES = (
        ('checkbox', _('Checkbox')),
        ('checkboxes', _('Checkboxes')),
        ('multiselect', _('Multiple select')),
        ('radio', _('Radio buttons')),
    )
    field_type = models.CharField(
        verbose_name='field type',
        max_length=16,
        choices=CHOICES
    )


class Poll(QuestionnairePage, AbstractForm):
    template = "poll/poll.html"
    parent_page_types = ["home.HomePage", "home.Section", "home.Article"]

    show_results = models.BooleanField(
        default=True, help_text=_("This option allows the users to see the results.")
    )
    result_as_percentage = models.BooleanField(
        default=True,
        help_text=_(
            "If not checked, the results will be shown as a total instead of a percentage."
        ),
    )

    # TODO allow randomising option ?
    # randomise_options = models.BooleanField(
    #     default=False,
    #     help_text=_(
    #         "Randomising the options allows the options to be shown in a different "
    #         "order each time the page is displayed."
    #     ),
    # )

    content_panels = Page.content_panels + [
        FormSubmissionsPanel(),
        MultiFieldPanel(
            [
                FieldPanel("allow_anonymous_submissions"),
                FieldPanel("show_results"),
                FieldPanel("result_as_percentage"),
                FieldPanel("allow_multiple_submissions"),
                FieldPanel("submit_button_text"),
            ],
            heading=_(
                "General settings for poll",
            ),
        ),
        MultiFieldPanel(
            [
                StreamFieldPanel("description"),
            ],
            heading=_(
                "Description at poll page",
            ),
        ),
        MultiFieldPanel(
            [
                StreamFieldPanel("thank_you_text"),
            ],
            heading="Description at thank you page",
        ),
        InlinePanel("poll_form_fields", label="Poll Form fields", min_num=1, max_num=1),
    ]

    class Meta:
        verbose_name = "Poll"
        verbose_name_plural = "Polls"

    def get_form_fields(self):
        return self.poll_form_fields.all()

    def get_submission_class(self):
        return UserSubmission

    def process_form_submission(self, form):
        self.get_submission_class().objects.create(
            form_data=json.dumps(form.cleaned_data, cls=DjangoJSONEncoder),
            page=self,
            user=form.user,
        )

    def serve(self, request, *args, **kwargs):
        if (
            not self.allow_multiple_submissions
            and self.get_submission_class()
            .objects.filter(page=self, user__pk=request.user.pk)
            .exists()
        ):
            return render(request, self.template, self.get_context(request))

        return super().serve(request, *args, **kwargs)

    def get_context(self, request, *args, **kwargs):
        context = super().get_context(request, *args, **kwargs)
        results = dict()

        # Get information about form fields
        data_fields = [
            (field.clean_name, field.label)
            for field in self.get_form_fields()
        ]

        submissions = self.get_submission_class().objects.filter(page=self)
        for submission in submissions:
            data = submission.get_data()

            # Count results for each question
            for name, label in data_fields:
                answer = data.get(name)
                if answer is None:
                    # Something wrong with data.
                    # Probably you have changed questions
                    # and now we are receiving answers for old questions.
                    # Just skip them.
                    continue

                if type(answer) is list:
                    # Answer is a list if the field type is 'Checkboxes'
                    answer = u', '.join(answer)

                question_stats = results.get(label, {})
                question_stats[answer] = question_stats.get(answer, 0) + 1
                results[label] = question_stats

        if self.result_as_percentage:
            total_submissions = len(submissions)
            for key in results:
                for k,v in results[key].items():
                    results[key][k] = round(v/total_submissions, 4) * 100

        context.update({
            'results': results,
            'back_url': request.GET.get('back_url'),
        })
        return context

    def get_data_fields(self):
        data_fields = [
            ('user', _('User')),
            ('submit_time', _('Submission Date')),
            ('page_url', _('Page URL')),
        ]
        data_fields += [
            (field.clean_name, field.label)
            for field in self.get_form_fields()
        ]
        return data_fields


class QuizFormField(AbstractFormField):
    page = ParentalKey("Quiz", on_delete=models.CASCADE, related_name="quiz_form_fields")
    required = models.BooleanField(verbose_name=_('required'), default=True)
    admin_label = models.CharField(
        verbose_name=_('admin_label'),
        max_length=256,
        help_text=_('Column header used during CSV export of survey '
                    'responses.'),
    )
    skip_logic = SkipLogicField()
    page_break = models.BooleanField(
        default=False,
        help_text=_(
            'Inserts a page break which puts the next question onto a new page'
        )
    )
    correct_answer = models.CharField(verbose_name=_('correct_answer'),
                                      max_length=256,
                                      help_text=_('Please provide correct answer for this question'))
    feedback = models.CharField(verbose_name=_('Feedback'),
                                max_length=255,
                                help_text=_('Feedback message for user answer.'),
                                null=True,
                                blank=True)

    panels = [
        FieldPanel('label'),
        FieldPanel('help_text'),
        FieldPanel('required'),
        FieldPanel('field_type', classname="formbuilder-type"),
        StreamFieldPanel('skip_logic'),
        FieldPanel('default_value', classname="formbuilder-default"),
        FieldPanel('correct_answer'),
        FieldPanel('feedback'),
        FieldPanel('admin_label'),
        FieldPanel('page_break'),
    ]

    @property
    def has_skipping(self):
        return any(
            logic.value['skip_logic'] != SkipState.NEXT
            for logic in self.skip_logic
        )

    def choice_index(self, choice):
        if choice:
            if self.field_type == 'checkbox':
                # clean checkboxes have True/False
                try:
                    return ['on', 'off'].index(choice)
                except ValueError:
                    return [True, False].index(choice)
            return self.choices.split(',').index(choice)
        else:
            return False

    def next_action(self, choice):
        return self.skip_logic[self.choice_index(choice)].value['skip_logic']

    def is_next_action(self, choice, *actions):
        if self.has_skipping:
            return self.next_action(choice) in actions
        return False

    def next_page(self, choice):
        logic = self.skip_logic[self.choice_index(choice)]
        return logic.value[self.next_action(choice)]


class Quiz(QuestionnairePage, AbstractForm):
    base_form_class = QuizForm

    parent_page_types = ["home.HomePage", "home.Section", "home.Article"]
    template = "quizzes/quiz.html"

    multi_step = models.BooleanField(
        default=False,
        verbose_name="Multi-step",
        help_text="Whether to display the survey questions to the user one at"
        " a time, instead of all at once.",
    )

    content_panels = Page.content_panels + [
        FormSubmissionsPanel(),
        MultiFieldPanel(
            [
                FieldPanel("allow_anonymous_submissions"),
                FieldPanel("allow_multiple_submissions"),
                FieldPanel("submit_button_text"),
                FieldPanel("multi_step"),
            ],
            heading=_(
                "General settings for quiz",
            ),
        ),
        MultiFieldPanel(
            [
                StreamFieldPanel("description"),
            ],
            heading=_(
                "Description at quiz page",
            ),
        ),
        MultiFieldPanel(
            [
                StreamFieldPanel("thank_you_text"),
            ],
            heading="Description at thank you page",
        ),
        InlinePanel("quiz_form_fields", label="Form fields"),
    ]

    @cached_property
    def has_page_breaks(self):
        return any(
            field.page_break
            for field in self.get_form_fields()
        )

    def get_form_class_for_step(self, step):
        return self.form_builder(step.object_list).get_form_class()

    def get_form_fields(self):
        return self.quiz_form_fields.all()

    def get_submission_class(self):
        return UserSubmission

    def process_form_submission(self, form):
        self.get_submission_class().objects.create(
            form_data=json.dumps(form.cleaned_data, cls=DjangoJSONEncoder),
            page=self,
            user=form.user,
        )

    def get_data_fields(self):
        data_fields = [
            ('user', _('User')),
            ('submit_time', _('Submission Date')),
            ('page_url', _('Page URL')),
        ]
        data_fields += [
            (field.clean_name, field.admin_label)
            for field in self.get_form_fields()
        ]
        return data_fields

    def get_context(self, request, *args, **kwargs):
        context = super().get_context(request, *args, **kwargs)
        context.update({'back_url': request.GET.get('back_url')})

        if request.method == 'POST':
            form_class = self.get_form_class()
            form = form_class(data=request.POST, page=self, user=request.user)

            for k, v in form.fields.items():
                form.fields[k].widget.attrs['readonly'] = True

            fields_info = {}

            total = 0
            total_correct = 0
            for field in self.get_form_fields():
                # TODO: handle multi-value case
                is_correct = form.data.get(field.clean_name) == field.correct_answer
                if is_correct:
                    total_correct += 1
                total += 1
                fields_info[field.clean_name] = {
                    'feedback': field.feedback,
                    'correct_answer': field.correct_answer,
                    'is_correct': is_correct,
                }

            context['form'] = form
            context['fields_info'] = fields_info
            context['result'] = {
                'total': total,
                'total_correct': total_correct,
            }

        return context

    class Meta:
        verbose_name = "quiz"
        verbose_name_plural = "quizzes"
