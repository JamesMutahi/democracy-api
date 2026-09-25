import random
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.survey.models import (
    Choice,
    ChoiceAnswer,
    Page,
    Question,
    Response,
    Survey,
    TextAnswer,
)


OPTION_TOPICS = [
    {
        "key": "Water",
        "choice": "Water supply",
        "challenges": [
            "Water shortage is the biggest challenge.",
            "Maji ni tatizo kubwa hapa.",
            "We lack clean drinking water.",
            "Tunahitaji maji safi kwa ward hii.",
            "Water points are too far from homes.",
            "Maji haitoki kwa wiki nzima.",
            "The water kiosk is broken.",
            "Children walk long distances to fetch water.",
        ],
        "actions": [
            "Fix water pipelines first.",
            "Drill more boreholes and add water tanks.",
            "Tunahitaji visima na mabomba ya maji.",
            "Repair the broken water kiosk.",
            "Bring piped water closer to homes.",
            "Water should be the first budget priority.",
        ],
    },
    {
        "key": "Roads",
        "choice": "Roads and drainage",
        "challenges": [
            "Roads become impassable during rain.",
            "Barabara zimeharibika sana.",
            "Flooding happens because drainage is poor.",
            "Boda riders struggle with muddy roads.",
            "Dust is affecting businesses and homes.",
            "Feeder roads are neglected.",
            "Barabara ya sokoni ni hatari kwa watoto.",
        ],
        "actions": [
            "Grade the feeder roads before the rainy season.",
            "Build proper drainage to stop flooding.",
            "Tunahitaji barabara nzuri kwa shule na hospitali.",
            "Maintain roads regularly.",
            "Tarmac the main market road.",
            "Roads should be prioritized for transport of goods.",
        ],
    },
    {
        "key": "Markets",
        "choice": "Markets and trade",
        "challenges": [
            "Market stalls are too few.",
            "Soko linahitaji usafi na usalama.",
            "Market fees are too high.",
            "Traders lack shade and toilets.",
            "Soko la sasa ni crowded and disorganized.",
            "Vendors need storage and cold rooms.",
        ],
        "actions": [
            "Build a modern market for small traders.",
            "Reduce market fees for small businesses.",
            "Soko linahitaji mabehewa na usafi.",
            "Add shade, toilets, and security at the market.",
            "Create spaces for youth and women businesses.",
            "Support local traders with better facilities.",
        ],
    },
    {
        "key": "Health",
        "choice": "Health services",
        "challenges": [
            "Health facilities are too far.",
            "Clinics lack drugs and staff.",
            "Healthcare is expensive for many families.",
            "Zahanati haina dawa za kutosha.",
            "Patients wait for too long at the clinic.",
            "Maternal healthcare needs improvement.",
        ],
        "actions": [
            "Improve drug supply at local clinics.",
            "Hire more nurses and clinical officers.",
            "Build a health center closer to the village.",
            "Tunahitaji huduma bora za afya.",
            "Reduce hospital fees for low-income residents.",
            "Support maternal and child health services.",
        ],
    },
    {
        "key": "Education",
        "choice": "Education and youth",
        "challenges": [
            "Schools lack classrooms and teachers.",
            "Youth unemployment is high.",
            "Vijana hawana ajira.",
            "School fees are still a burden.",
            "Libraries and youth centers are missing.",
            "Children walk far to reach school.",
        ],
        "actions": [
            "Build more classrooms and hire teachers.",
            "Support youth businesses with funds and training.",
            "Create youth spaces and digital hubs.",
            "Eliminate hidden school costs.",
            "Tunahitaji elimu bora kwa watoto wetu.",
            "Provide bursaries for needy students.",
        ],
    },
]

MULTIPLE_CHOICE_OPTIONS = [
    "Health facility",
    "Market",
    "Water point",
    "Public transport",
    "School",
    "None",
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
    help = "Creates a test survey with sample responses for development/testing."

    def add_arguments(self, parser):
        parser.add_argument(
            "--title",
            type=str,
            default="Test Survey: County Priorities",
        )
        parser.add_argument(
            "--responses",
            type=int,
            default=80,
            help="Number of survey responses to create.",
        )
        parser.add_argument(
            "--text-rate",
            type=float,
            default=0.9,
            help="Probability that a respondent answers each open-text question.",
        )
        parser.add_argument(
            "--pii-rate",
            type=float,
            default=0.2,
            help="Probability that an open-text answer includes fake PII.",
        )
        parser.add_argument(
            "--ended",
            action="store_true",
            help="Make the survey already ended.",
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
            start_time = now - timedelta(days=3)
            end_time = now - timedelta(hours=1)
        else:
            start_time = now - timedelta(hours=1)
            end_time = now + timedelta(days=3)

        with transaction.atomic():
            survey = (
                Survey.objects.filter(title=opts["title"])
                .order_by("-id")
                .first()
            )

            if survey:
                survey.description = (
                    "Test survey generated for local development. "
                    "Questions and responses are fake."
                )
                survey.county = None
                survey.constituency = None
                survey.ward = None
                survey.start_time = start_time
                survey.end_time = end_time
                survey.is_active = True
                survey.save()

                # Remove old answers before removing pages/questions.
                Response.objects.filter(survey=survey).delete()

                # Remove old structure.
                survey.pages.all().delete()
            else:
                survey = Survey.objects.create(
                    title=opts["title"],
                    description=(
                        "Test survey generated for local development. "
                        "Questions and responses are fake."
                    ),
                    county=None,
                    constituency=None,
                    ward=None,
                    start_time=start_time,
                    end_time=end_time,
                    is_active=True,
                )

            # ──────────────────────────────────────────────
            # Page 1: Service Access
            # ──────────────────────────────────────────────
            page_1 = Page.objects.create(
                survey=survey,
                number=1,
                title="Service Access",
            )

            single_choice_question = Question.objects.create(
                page=page_1,
                number=1,
                type=Question.Type.SINGLE_CHOICE,
                text="Which area should receive priority investment?",
                hint="Choose one option only.",
                is_required=True,
            )

            single_choice_map = {}

            for index, topic in enumerate(OPTION_TOPICS, start=1):
                choice = Choice.objects.create(
                    question=single_choice_question,
                    number=index,
                    text=topic["choice"],
                )
                single_choice_map[topic["key"]] = choice

            multiple_choice_question = Question.objects.create(
                page=page_1,
                number=2,
                type=Question.Type.MULTIPLE_CHOICE,
                text="Which public services have you used in the last 6 months?",
                hint="Select all that apply.",
                is_required=False,
            )

            multiple_choice_map = {}

            for index, text in enumerate(MULTIPLE_CHOICE_OPTIONS, start=1):
                choice = Choice.objects.create(
                    question=multiple_choice_question,
                    number=index,
                    text=text,
                )
                multiple_choice_map[text] = choice

            number_question = Question.objects.create(
                page=page_1,
                number=3,
                type=Question.Type.NUMBER,
                text="How many hours per week do you spend collecting or waiting for water?",
                hint="Enter 0 if this does not apply to you.",
                is_required=False,
            )

            # ──────────────────────────────────────────────
            # Page 2: Community Feedback
            # ──────────────────────────────────────────────
            page_2 = Page.objects.create(
                survey=survey,
                number=2,
                title="Community Feedback",
            )

            challenge_question = Question.objects.create(
                page=page_2,
                number=1,
                type=Question.Type.TEXT,
                text="What is the biggest challenge in your ward?",
                hint="Write freely. Do not include personal information.",
                is_required=False,
            )

            action_question = Question.objects.create(
                page=page_2,
                number=2,
                type=Question.Type.TEXT,
                text="What should the county government do first?",
                hint="Write freely. Do not include personal information.",
                is_required=False,
            )

            text_answers = []
            choice_answers = []

            for i in range(opts["responses"]):
                user = self._get_or_create_user(User, i)

                start_time_response = self._random_response_start_time(survey, now)
                end_time_response = start_time_response + timedelta(
                    minutes=random.randint(2, 20)
                )

                response = Response.objects.create(
                    user=user,
                    survey=survey,
                    start_time=start_time_response,
                    end_time=end_time_response,
                )

                topic = random.choice(OPTION_TOPICS)

                # Required single-choice answer.
                choice_answers.append(
                    ChoiceAnswer(
                        response=response,
                        question=single_choice_question,
                        choice=single_choice_map[topic["key"]],
                    )
                )

                # Optional multiple-choice answers.
                selected_services = self._pick_multiple_choices()

                for service in selected_services:
                    choice_answers.append(
                        ChoiceAnswer(
                            response=response,
                            question=multiple_choice_question,
                            choice=multiple_choice_map[service],
                        )
                    )

                # Optional number answer.
                if random.random() <= 0.9:
                    if topic["key"] == "Water":
                        hours = random.randint(4, 25)
                    else:
                        hours = random.randint(0, 12)

                    text_answers.append(
                        TextAnswer(
                            response=response,
                            question=number_question,
                            text=str(hours),
                        )
                    )

                # Optional open-text answers.
                if random.random() <= opts["text_rate"]:
                    challenge_text = self._make_text(
                        topic["challenges"],
                        opts["pii_rate"],
                    )

                    text_answers.append(
                        TextAnswer(
                            response=response,
                            question=challenge_question,
                            text=challenge_text,
                        )
                    )

                if random.random() <= opts["text_rate"]:
                    action_text = self._make_text(
                        topic["actions"],
                        opts["pii_rate"],
                    )

                    text_answers.append(
                        TextAnswer(
                            response=response,
                            question=action_question,
                            text=action_text,
                        )
                    )

            TextAnswer.objects.bulk_create(text_answers, batch_size=1000)
            ChoiceAnswer.objects.bulk_create(choice_answers, batch_size=1000)

        self.stdout.write(
            self.style.SUCCESS(
                f"Created/updated survey id={survey.id} title='{survey.title}'"
            )
        )
        self.stdout.write(f"Responses: {opts['responses']}")
        self.stdout.write(f"Text answers: {len(text_answers)}")
        self.stdout.write(f"Choice answers: {len(choice_answers)}")
        self.stdout.write(f"Survey ended: {opts['ended']}")
        self.stdout.write("Test user password: TestPass123!")

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

    def _random_response_start_time(self, survey: Survey, now):
        if survey.end_time and survey.end_time <= now:
            base_end = survey.end_time

            return base_end - timedelta(
                hours=random.randint(1, 48),
                minutes=random.randint(0, 59),
            )

        return now - timedelta(
            hours=random.randint(1, 48),
            minutes=random.randint(0, 59),
        )

    def _pick_multiple_choices(self):
        # Occasionally the respondent has not used any service.
        if random.random() < 0.08:
            return ["None"]

        choices_without_none = [
            choice for choice in MULTIPLE_CHOICE_OPTIONS if choice != "None"
        ]

        count = random.randint(1, 3)

        return random.sample(choices_without_none, k=count)

    def _make_text(self, texts, pii_rate: float) -> str:
        text = random.choice(texts)

        if random.random() < 0.2:
            text = f"{text} {random.choice(FOLLOW_UPS)}"

        if random.random() < pii_rate:
            text = f"{text}{random.choice(PII_SNIPPETS)}"

        return text