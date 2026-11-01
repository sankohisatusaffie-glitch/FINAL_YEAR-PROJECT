import re

from django import forms
from django.db.models import Case, Count, IntegerField, Q, Value, When
from django.contrib.auth import password_validation
from django.contrib.auth.forms import AuthenticationForm
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator
from django.utils import timezone

from .models import (
    Announcement,
    Attendance,
    ClientProfile,
    Company,
    Complaint,
    Incident,
    MaterialRequest,
    Message,
    Phase,
    Project,
    Rating,
    Service,
    ServiceRequest,
    Site,
    SiteReport,
    Skill,
    Task,
    User,
    Worker,
    validate_image_upload,
    validate_upload,
)


MIN_WORKER_AGE = 18
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
DOCUMENT_ACCEPT = ".pdf,.png,.jpg,.jpeg,.webp"
IMAGE_ACCEPT = ".png,.jpg,.jpeg,.webp"

PHONE_MESSAGE = "Enter a Sierra Leone phone number, for example 076 123 456 or +232 76 123 456."
MOBILE_MESSAGE = "Enter a Sierra Leone mobile number, for example 076 123 456. Landlines cannot receive SMS or mobile money."
NAME_MESSAGE = "Use letters only, with spaces, hyphens or apostrophes between names."
USERNAME_MESSAGE = "Use letters, numbers and @ . + - _ only, with no spaces."
NAME_PATTERN = re.compile(r"[^\W\d_]+(?:[\s'’.-]+[^\W\d_]+)*\.?")
PHONE_PATTERN = re.compile(r"\+?[\d\s()-]+")
REPEATED_LETTERS = re.compile(r"([^\W\d_])\1\1", re.IGNORECASE)

# Sierra Leone numbers are +232 followed by 8 digits. National numbers starting with 2 are
# fixed lines; 3, 7, 8 and 9 are mobile networks. Other first digits are not in use.
SL_FIRST_DIGITS = "23789"
SL_MOBILE_FIRST_DIGITS = "3789"

# Throwaway inbox services: an account on one of these cannot be reached later.
DISPOSABLE_EMAIL_DOMAINS = {
    "10minutemail.com", "20minutemail.com", "dispostable.com", "emailondeck.com", "fakeinbox.com", "getnada.com",
    "guerrillamail.com", "guerrillamail.net", "maildrop.cc", "mailinator.com", "mailnesia.com", "mintemail.com",
    "mohmal.com", "moakt.com", "sharklasers.com", "spamgourmet.com", "temp-mail.org", "tempail.com", "tempmail.com",
    "tempmailo.com", "throwawaymail.com", "trashmail.com", "yopmail.com",
}

# HTML `pattern` attributes mirror the server-side validators so the browser can
# give feedback before submitting. The Python validators remain authoritative.
PHONE_ATTRS = {
    "type": "tel", "inputmode": "tel", "autocomplete": "tel", "placeholder": "076 123 456",
    "pattern": r"\s*(\+?232|00232|0)?[\s\-]*[23789]([\s\-]*[0-9]){7}\s*", "data-pattern-message": PHONE_MESSAGE,
}
NAME_ATTRS = {"pattern": r"\p{L}+(?:[\s'’.\-]+\p{L}+)*\.?", "data-pattern-message": NAME_MESSAGE}
UPLOAD_ATTRS = {"accept": DOCUMENT_ACCEPT, "data-max-size": MAX_UPLOAD_BYTES}
IMAGE_UPLOAD_ATTRS = {"accept": IMAGE_ACCEPT, "data-max-size": MAX_UPLOAD_BYTES}


def sierra_leone_number(value):
    """The 8-digit national part of a Sierra Leone phone number, or None if it is not one."""
    digits = re.sub(r"\D", "", value or "")
    if digits.startswith("00232"):
        digits = digits[2:]
    if len(digits) == 11 and digits.startswith("232"):
        national = digits[3:]
    elif len(digits) == 9 and digits.startswith("0"):
        national = digits[1:]
    elif len(digits) == 8:
        national = digits
    else:
        return None
    if national[0] not in SL_FIRST_DIGITS:
        return None
    if len(set(national)) == 1 or national in "0123456789012345678" or national in "9876543210987654321":
        return None  # 77777777, 12345678 and similar are placeholders, not real numbers.
    return national


def format_phone(national):
    return f"+232 {national[:2]} {national[2:5]} {national[5:]}"


def same_phone(a, b):
    first, second = sierra_leone_number(a), sierra_leone_number(b)
    return first is not None and first == second


def validate_phone(value):
    if not PHONE_PATTERN.fullmatch(value) or sierra_leone_number(value) is None:
        raise ValidationError(PHONE_MESSAGE)


def validate_person_name(value):
    if not NAME_PATTERN.fullmatch(value):
        raise ValidationError(NAME_MESSAGE)
    if sum(char.isalpha() for char in value) < 2:
        raise ValidationError("Enter the full name, not an initial.")
    if REPEATED_LETTERS.search(value):
        raise ValidationError("This does not look like a real name. Check the spelling.")


def validate_place(value):
    if sum(char.isalpha() for char in value) < 3:
        raise ValidationError("Enter a real place or street name.")
    if REPEATED_LETTERS.search(value):
        raise ValidationError("This does not look like a real address. Check the spelling.")


def validate_real_email(value):
    domain = value.rsplit("@", 1)[-1].lower()
    if domain in DISPOSABLE_EMAIL_DOMAINS or any(domain.endswith(f".{blocked}") for blocked in DISPOSABLE_EMAIL_DOMAINS):
        raise ValidationError("Use a permanent email address; temporary inbox services are not accepted.")


def validate_emergency_contact(value):
    if not any(sierra_leone_number(match) for match in re.findall(r"\+?\d[\d\s\-]{6,16}\d", value)):
        raise ValidationError("Include the contact's phone number, for example: Fatu Sesay (sister) 076 123 456.")
    if sum(char.isalpha() for char in value) < 2:
        raise ValidationError("Include the contact's name as well as their number.")


def tidy_spaces(value):
    return re.sub(r"\s+", " ", value).strip()


def tidy_name(value):
    """Collapse spaces and fix names typed all in lower or upper case (mary-jane → Mary-Jane)."""
    value = tidy_spaces(value)
    if value.islower() or value.isupper():
        value = re.sub(r"(^|[\s'’-])([^\W\d_])", lambda match: match.group(1) + match.group(2).upper(), value.lower())
    return value


def tidy_place(value):
    """Collapse spaces and capitalise words of an address typed all in lower case."""
    value = tidy_spaces(value)
    if value.islower():
        value = " ".join(word[:1].upper() + word[1:] if word[:1].isalpha() else word for word in value.split(" "))
    return value


def tidy_code(value):
    return tidy_spaces(value).upper()


PERSON_NAME_FIELDS = {"first_name", "middle_name", "last_name", "contact_person"}
PLACE_FIELDS = {"address", "area", "city", "location"}
CODE_FIELDS = {"worker_id", "registration_number"}


class PhoneField(forms.CharField):
    """A Sierra Leone phone number, stored in one consistent format: +232 76 123 456."""

    def __init__(self, *args, mobile_only=False, **kwargs):
        self.mobile_only = mobile_only
        kwargs.setdefault("max_length", 40)
        super().__init__(*args, **kwargs)

    def clean(self, value):
        value = super().clean(value)
        if not value:
            return value
        national = sierra_leone_number(value)
        if national is None:
            raise ValidationError(PHONE_MESSAGE, code="invalid")
        if self.mobile_only and national[0] not in SL_MOBILE_FIRST_DIGITS:
            raise ValidationError(MOBILE_MESSAGE, code="landline")
        return format_phone(national)


def validate_established_year(value):
    current_year = timezone.localdate().year
    if value is not None and not 1800 <= value <= current_year:
        raise ValidationError(f"Enter a year between 1800 and {current_year}.")


def years_ago(years, today=None):
    today = today or timezone.localdate()
    try:
        return today.replace(year=today.year - years)
    except ValueError:  # 29 February in a non-leap year
        return today.replace(year=today.year - years, day=28)


def age_on(born, day):
    return day.year - born.year - ((day.month, day.day) < (born.month, born.day))


# Construction trades, shared by worker records and service requests so recommendations match.
TRADE_CHOICES = [
    ("General construction", "General construction"),
    ("Mason", "Mason / bricklayer"),
    ("Carpenter", "Carpenter / joiner"),
    ("Electrician", "Electrician"),
    ("Plumber", "Plumber"),
    ("Painter", "Painter / decorator"),
    ("Tiler", "Tiler"),
    ("Welder", "Welder / metal worker"),
    ("Steel fixer", "Steel fixer"),
    ("Roofer", "Roofer"),
    ("Plasterer", "Plasterer"),
    ("Concrete worker", "Concrete worker"),
    ("Heavy equipment operator", "Heavy equipment operator"),
    ("Survey assistant", "Surveyor / survey assistant"),
    ("HVAC technician", "HVAC / air-conditioning technician"),
    ("Glazier", "Glazier / glass installer"),
    ("Landscaper", "Landscaper"),
    ("Demolition worker", "Demolition worker"),
    ("Site supervisor", "Site supervisor"),
    ("General laborer", "General laborer"),
]


class StyledFormMixin:
    """Adds the shared input class, per-field HTML attributes and validators, and tidying.

    ``field_attrs`` maps field names to widget attributes; a ``type`` entry changes
    the input type. ``field_validators`` adds validators to generated model fields.
    ``phone_fields`` maps field names to True (mobile only) or False (any SL number);
    those fields are validated as Sierra Leone numbers and saved as +232 76 123 456.
    ``sections`` groups fields for display as (title, description, field names).
    Person names, places and codes are tidied by field name when the form is cleaned.
    """

    field_attrs = {}
    field_validators = {}
    phone_fields = {}
    sections = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, mobile_only in self.phone_fields.items():
            if name in self.fields:
                old = self.fields[name]
                self.fields[name] = PhoneField(
                    required=old.required, label=old.label, help_text=old.help_text, initial=old.initial,
                    widget=old.widget, mobile_only=mobile_only,
                )
        for name, field in self.fields.items():
            attrs = dict(self.field_attrs.get(name, {}))
            if name in self.phone_fields:
                attrs = {**PHONE_ATTRS, **attrs}
            if "type" in attrs:
                field.widget.input_type = attrs.pop("type")
            if not isinstance(field.widget, (forms.CheckboxInput, forms.CheckboxSelectMultiple, forms.RadioSelect)):
                existing = field.widget.attrs.get("class", "")
                field.widget.attrs["class"] = f"{existing} field-control".strip()
            field.widget.attrs.update(attrs)
            extra = list(self.field_validators.get(name, []))
            if name in PERSON_NAME_FIELDS and isinstance(field, forms.CharField) and validate_person_name not in field.validators:
                extra.append(validate_person_name)
            if name in PLACE_FIELDS and isinstance(field, forms.CharField):
                extra.append(validate_place)
            field.validators = [*field.validators, *extra]

    def clean(self):
        cleaned = super().clean()
        for name, value in list(cleaned.items()):
            if not isinstance(value, str) or not value or name not in self.fields:
                continue
            if name in PERSON_NAME_FIELDS:
                cleaned[name] = tidy_name(value)
            elif name in PLACE_FIELDS:
                cleaned[name] = tidy_place(value)
            elif name in CODE_FIELDS:
                cleaned[name] = tidy_code(value)
            elif name in ("name", "company_type", "contact_position", "organization_name", "primary_trade", "expected_duration"):
                cleaned[name] = tidy_spaces(value)
        return cleaned


def phone_in_use(phone, numbers):
    """True if `phone` matches any of the stored numbers, however they were typed."""
    target = sierra_leone_number(phone)
    return target is not None and any(sierra_leone_number(number) == target for number in numbers if number)


class RegistrationFields(StyledFormMixin, forms.Form):
    username = forms.CharField(max_length=150, help_text="You will use this to log in. Letters, numbers and @ . + - _ only.")
    email = forms.EmailField(max_length=254, validators=[validate_real_email])
    first_name = forms.CharField(max_length=150, validators=[validate_person_name])
    last_name = forms.CharField(max_length=150, validators=[validate_person_name])
    password = forms.CharField(
        widget=forms.PasswordInput, min_length=8,
        help_text="At least 8 characters. Avoid common passwords, numbers only, or anything similar to your name.",
    )
    password_confirm = forms.CharField(widget=forms.PasswordInput, label="Confirm password")

    field_attrs = {
        "username": {
            "autocomplete": "username", "autocapitalize": "none", "spellcheck": "false",
            "pattern": r"[\p{L}\p{N}_.@+\-]+", "data-pattern-message": USERNAME_MESSAGE,
        },
        "email": {"autocomplete": "email", "placeholder": "name@example.com"},
        "first_name": {**NAME_ATTRS, "autocomplete": "given-name"},
        "last_name": {**NAME_ATTRS, "autocomplete": "family-name"},
        "password": {"autocomplete": "new-password"},
        "password_confirm": {"autocomplete": "new-password", "data-match": "password", "data-match-message": "The passwords do not match."},
    }

    sections = [
        ("Account details", "The person who will sign in and manage this account.", ["username", "email", "first_name", "last_name", "password", "password_confirm"]),
    ]

    def clean_username(self):
        from .models import User
        value = self.cleaned_data["username"].strip()
        User._meta.get_field("username").run_validators(value)
        if User.objects.filter(username__iexact=value).exists():
            raise ValidationError("This username is already in use.")
        return value

    def clean_email(self):
        from .models import User
        email = self.cleaned_data["email"].lower().strip()
        if User.objects.filter(email__iexact=email).exists():
            raise ValidationError("An account with this email already exists.")
        return email

    def clean(self):
        cleaned = super().clean()
        password = cleaned.get("password")
        confirmation = cleaned.get("password_confirm")
        if password and confirmation and password != confirmation:
            self.add_error("password_confirm", "The passwords do not match.")
        if password:
            try:
                from .models import User
                candidate = User(
                    username=cleaned.get("username", ""), email=cleaned.get("email", ""),
                    first_name=cleaned.get("first_name", ""), last_name=cleaned.get("last_name", ""),
                )
                password_validation.validate_password(password, candidate)
            except ValidationError as exc:
                self.add_error("password", exc)
        return cleaned


class CompanyRegistrationForm(RegistrationFields):
    name = forms.CharField(max_length=160, label="Company name")
    registration_number = forms.CharField(max_length=90, label="Registration number")
    company_type = forms.CharField(max_length=100, required=False)
    established_year = forms.IntegerField(required=False, label="Year established", validators=[validate_established_year])
    description = forms.CharField(widget=forms.Textarea(attrs={"rows": 4}), max_length=3000, required=False, label="About the company")
    telephone = forms.CharField(max_length=40)
    contact_person = forms.CharField(max_length=140, validators=[validate_person_name])
    contact_position = forms.CharField(max_length=100, required=False)
    address = forms.CharField(max_length=240)
    city = forms.CharField(max_length=100, initial="Freetown")
    area = forms.CharField(max_length=120, required=False, label="Area / community")
    services = forms.ModelMultipleChoiceField(queryset=Service.objects.filter(active=True), required=False, widget=forms.CheckboxSelectMultiple)
    logo = forms.FileField(required=False, validators=[validate_image_upload], help_text="PNG, JPG or WEBP image · maximum 5 MB.")
    registration_document = forms.FileField(
        required=False, label="Company registration document", validators=[validate_upload],
        help_text="PDF or image · maximum 5 MB. Only administrators can see this file.",
    )

    field_attrs = {
        **RegistrationFields.field_attrs,
        "name": {"autocomplete": "organization"},
        "registration_number": {"autocapitalize": "characters", "spellcheck": "false"},
        "company_type": {"placeholder": "e.g. Building contractor"},
        "established_year": {"min": 1800, "placeholder": "e.g. 2012"},
        "contact_person": {**NAME_ATTRS, "autocomplete": "name"},
        "contact_position": {"autocomplete": "organization-title"},
        "address": {"autocomplete": "street-address"},
        "city": {"autocomplete": "address-level2"},
        "logo": IMAGE_UPLOAD_ATTRS,
        "registration_document": UPLOAD_ATTRS,
    }
    sections = [
        *RegistrationFields.sections,
        ("Company details", "How your company appears to clients once approved.", ["name", "registration_number", "company_type", "established_year", "description"]),
        ("Contact and location", "Where clients and administrators can reach you.", ["contact_person", "contact_position", "telephone", "city", "address", "area"]),
        ("Services", "Select every type of work your company offers.", ["services"]),
        ("Logo and documents", "Your registration document helps administrators verify the company faster.", ["logo", "registration_document"]),
    ]
    # Keep the address half width here so it pairs with "Area / community".
    wide_fields = {"description"}
    phone_fields = {"telephone": False}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["established_year"].widget.attrs["max"] = timezone.localdate().year

    def clean_registration_number(self):
        value = tidy_code(self.cleaned_data["registration_number"])
        if Company.objects.filter(registration_number__iexact=value).exists():
            raise ValidationError("A company with this registration number is already registered.")
        return value

    def clean_telephone(self):
        value = self.cleaned_data["telephone"]
        if phone_in_use(value, Company.objects.values_list("telephone", flat=True)):
            raise ValidationError("Another company is already registered with this phone number.")
        return value


CLIENT_FIELD_ATTRS = {
    "address": {"autocomplete": "street-address"},
    "location": {"placeholder": "e.g. Lumley, Freetown"},
    "organization_name": {"autocomplete": "organization"},
}


class ClientRegistrationForm(RegistrationFields):
    telephone = forms.CharField(max_length=40)
    address = forms.CharField(max_length=240, required=False)
    location = forms.CharField(max_length=120, required=False)
    organization_name = forms.CharField(max_length=160, required=False)
    client_type = forms.ChoiceField(choices=ClientProfile.ClientType.choices)

    field_attrs = {**RegistrationFields.field_attrs, **CLIENT_FIELD_ATTRS}
    phone_fields = {"telephone": False}
    sections = [
        *RegistrationFields.sections,
        ("Contact information", "Helps companies get in touch about your requests.", ["telephone", "client_type", "organization_name", "location", "address"]),
    ]

    def clean_telephone(self):
        value = self.cleaned_data["telephone"]
        if phone_in_use(value, ClientProfile.objects.values_list("telephone", flat=True)):
            raise ValidationError("This phone number is already registered to another client account.")
        return value


class ClientProfileForm(StyledFormMixin, forms.ModelForm):
    field_attrs = CLIENT_FIELD_ATTRS
    phone_fields = {"telephone": False}
    sections = [
        ("Contact details", "Companies use these details to reach you about your requests.", ["telephone", "client_type", "organization_name"]),
        ("Location", "Your usual area helps companies plan site visits.", ["location", "address"]),
    ]

    class Meta:
        model = ClientProfile
        fields = ["telephone", "address", "location", "organization_name", "client_type"]
        widgets = {"address": forms.Textarea(attrs={"rows": 3})}

    def clean_telephone(self):
        value = self.cleaned_data["telephone"]
        others = ClientProfile.objects.exclude(pk=self.instance.pk).values_list("telephone", flat=True)
        if phone_in_use(value, others):
            raise ValidationError("This phone number is already registered to another client account.")
        return value


class CompanyProfileForm(StyledFormMixin, forms.ModelForm):
    field_attrs = {
        "name": {"autocomplete": "organization"},
        "established_year": {"min": 1800},
        "contact_person": {**NAME_ATTRS, "autocomplete": "name"},
        "address": {"autocomplete": "street-address"},
        "logo": IMAGE_UPLOAD_ATTRS,
    }
    field_validators = {"established_year": [validate_established_year]}
    phone_fields = {"telephone": False}
    sections = [
        ("Company details", "Shown on your public profile in the company directory.", ["name", "company_type", "established_year", "description"]),
        ("Contact", "The person clients and administrators should contact.", ["contact_person", "contact_position", "telephone"]),
        ("Location", "Helps clients find companies working near them.", ["city", "area", "address"]),
        ("Services", "Clients can only request services you list here.", ["services"]),
        ("Logo", "A clear logo helps clients recognise your company.", ["logo"]),
    ]

    class Meta:
        model = Company
        fields = ["name", "company_type", "established_year", "description", "services", "telephone", "contact_person", "contact_position", "address", "city", "area", "logo"]
        labels = {"established_year": "Year established", "area": "Area / community"}
        help_texts = {"logo": "PNG, JPG or WEBP image · maximum 5 MB."}
        widgets = {"description": forms.Textarea(attrs={"rows": 5}), "services": forms.CheckboxSelectMultiple()}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["established_year"].widget.attrs["max"] = timezone.localdate().year

    def clean_telephone(self):
        value = self.cleaned_data["telephone"]
        if phone_in_use(value, Company.objects.exclude(pk=self.instance.pk).values_list("telephone", flat=True)):
            raise ValidationError("Another company is already registered with this phone number.")
        return value


GENDER_CHOICES = [("", "Prefer not to say"), ("Female", "Female"), ("Male", "Male")]


class WorkerForm(StyledFormMixin, forms.ModelForm):
    primary_trade = forms.ChoiceField(choices=[("", "Select a trade"), *TRADE_CHOICES], label="Primary trade")
    gender = forms.ChoiceField(choices=GENDER_CHOICES, required=False)
    field_attrs = {
        "worker_id": {"autocapitalize": "characters", "spellcheck": "false", "placeholder": "e.g. W-014"},
        "first_name": NAME_ATTRS,
        "middle_name": NAME_ATTRS,
        "last_name": NAME_ATTRS,
        "telephone": {"autocomplete": "off"},
        "emergency_contact": {"placeholder": "e.g. Fatu Sesay (sister) 076 123 456"},
        "skills": {"placeholder": "e.g. Wiring, solar installation, conduit bending"},
        "experience_years": {"max": 60},
        "daily_rate": {"min": 0, "step": "0.01", "placeholder": "e.g. 250.00", "inputmode": "decimal"},
        "mobile_money_number": {"autocomplete": "off"},
        "photo": IMAGE_UPLOAD_ATTRS,
    }
    field_validators = {
        "experience_years": [MaxValueValidator(60)],
        "emergency_contact": [validate_emergency_contact],
    }
    phone_fields = {"telephone": False, "mobile_money_number": True}
    sections = [
        ("Identity", "Basic details used to identify this worker in your company.", ["worker_id", "gender", "first_name", "last_name", "middle_name", "date_of_birth", "photo"]),
        ("Contact", "How to reach the worker, and who to call in an emergency.", ["telephone", "emergency_contact", "address"]),
        ("Trade and experience", "Used to recommend this worker for matching tasks and projects.", ["primary_trade", "experience_years", "skill_set", "skills", "qualifications", "certifications"]),
        ("Employment", "Only active, available workers can be assigned to projects.", ["employment_status", "availability", "employment_date"]),
        ("Pay", "Wages are calculated from attendance hours and this daily rate.", ["daily_rate", "mobile_money_provider", "mobile_money_number"]),
    ]

    class Meta:
        model = Worker
        exclude = ["company", "created_at", "user"]
        labels = {
            "worker_id": "Worker ID", "experience_years": "Years of experience", "skills": "Other skills",
            "skill_set": "Skills", "daily_rate": "Daily rate (SLE)", "mobile_money_number": "Mobile money number",
        }
        help_texts = {
            "skills": "Anything not listed above, separated by commas.", "mobile_money_provider": "Used to pay wages directly to the worker.",
            "emergency_contact": "Name, relationship and Sierra Leone phone number of someone to call.",
        }
        widgets = {
            "date_of_birth": forms.DateInput(attrs={"type": "date"}),
            "employment_date": forms.DateInput(attrs={"type": "date"}),
            "skills": forms.Textarea(attrs={"rows": 2}),
            "qualifications": forms.Textarea(attrs={"rows": 3}),
            "certifications": forms.Textarea(attrs={"rows": 3}),
            "skill_set": forms.CheckboxSelectMultiple(),
        }

    def __init__(self, *args, company=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.company = company or (self.instance.company if self.instance.company_id else None)
        current = self.instance.primary_trade if self.instance.pk else ""
        if current and current not in dict(TRADE_CHOICES):
            # Keep a trade typed before this list existed, so saving never silently changes it.
            self.fields["primary_trade"].choices = [*self.fields["primary_trade"].choices, (current, f"{current} (current, not in list)")]
        gender = self.instance.gender if self.instance.pk else ""
        if gender:
            match = next((value for value, _ in GENDER_CHOICES if value.lower() == gender.strip().lower()), None)
            if match is not None:
                self.initial["gender"] = match  # "male" typed before the dropdown becomes "Male".
            else:
                self.fields["gender"].choices = [*GENDER_CHOICES, (gender, f"{gender} (current)")]
        today = timezone.localdate()
        self.fields["date_of_birth"].widget.attrs["max"] = years_ago(MIN_WORKER_AGE, today).isoformat()
        self.fields["employment_date"].widget.attrs["max"] = today.isoformat()

    def clean_worker_id(self):
        value = tidy_code(self.cleaned_data["worker_id"])
        duplicates = Worker.objects.filter(company=self.company, worker_id__iexact=value).exclude(pk=self.instance.pk)
        if self.company and duplicates.exists():
            raise ValidationError("Another worker in your company already uses this ID.")
        return value

    def clean_date_of_birth(self):
        value = self.cleaned_data.get("date_of_birth")
        if value:
            today = timezone.localdate()
            if value > today:
                raise ValidationError("Date of birth cannot be in the future.")
            if value > years_ago(MIN_WORKER_AGE, today):
                raise ValidationError(f"Workers must be at least {MIN_WORKER_AGE} years old.")
            if value < years_ago(100, today):
                raise ValidationError("Check the year — this date is more than 100 years ago.")
        return value

    def clean_employment_date(self):
        value = self.cleaned_data.get("employment_date")
        if value and value > timezone.localdate():
            raise ValidationError("Employment date cannot be in the future.")
        return value

    def clean_telephone(self):
        value = self.cleaned_data.get("telephone")
        if value and self.company:
            others = Worker.objects.filter(company=self.company).exclude(pk=self.instance.pk).values_list("telephone", flat=True)
            if phone_in_use(value, others):
                raise ValidationError("Another worker in your company already has this phone number.")
        return value

    def clean(self):
        cleaned = super().clean()
        today = timezone.localdate()
        born, employed = cleaned.get("date_of_birth"), cleaned.get("employment_date")
        if born and employed and age_on(born, employed) < 14:
            self.add_error("employment_date", "The worker would have been under 14 on this employment date. Check both dates.")
        experience = cleaned.get("experience_years")
        if born and experience is not None and experience > max(age_on(born, today) - 14, 0):
            self.add_error("experience_years", f"That is more experience than possible for a {age_on(born, today)}-year-old (counting work from age 14).")
        contact, phone = cleaned.get("emergency_contact", ""), cleaned.get("telephone", "")
        if contact and phone and any(same_phone(number, phone) for number in re.findall(r"\+?\d[\d\s\-]{6,16}\d", contact)):
            self.add_error("emergency_contact", "Use someone else's number for the emergency contact, not the worker's own.")
        if self.instance and self.instance.pk:
            active_assignments = self.instance.assignments.filter(status="active").exists()
            if active_assignments and cleaned.get("employment_status") == Worker.Employment.FORMER:
                raise ValidationError("Remove this worker from active projects before marking them as a former worker.")
            if active_assignments and cleaned.get("availability") != self.instance.availability:
                raise ValidationError("Remove this worker from active projects before changing their availability.")
        return cleaned


class ServiceRequestForm(StyledFormMixin, forms.ModelForm):
    service = forms.ModelChoiceField(
        queryset=Service.objects.filter(active=True),
        empty_label="Select a construction service",
        label="Type of construction service",
    )
    preferred_trade = forms.ChoiceField(
        label="Preferred trade",
        required=False,
        choices=[("", "Select a preferred trade (optional)"), *TRADE_CHOICES],
        help_text="Choose the trade you prefer, or leave this blank if you are unsure.",
    )

    field_attrs = {
        "attachment": UPLOAD_ATTRS,
        "location": {"placeholder": "e.g. 14 Wilkinson Road, Lumley"},
        "expected_duration": {"placeholder": "e.g. 6 weeks"},
    }
    sections = [
        ("The work", "Tell the company what you need and where.", ["service", "preferred_trade", "location"]),
        ("Timing", "Approximate dates are fine — you can agree details later.", ["preferred_start_date", "expected_duration"]),
        ("Supporting files", "Optional drawings, documents or site photos.", ["attachment"]),
    ]

    class Meta:
        model = ServiceRequest
        fields = ["service", "attachment", "location", "preferred_start_date", "expected_duration", "preferred_trade"]
        labels = {
            "attachment": "Supporting documents or images",
            "location": "Project location",
            "preferred_start_date": "Preferred start date",
            "expected_duration": "Expected duration",
        }
        help_texts = {
            "attachment": "Drawings, documents or site photos · PDF or image · maximum 5 MB.",
            "location": "Where will the construction work take place?",
            "preferred_start_date": "Choose a date if you have one in mind.",
            "expected_duration": "How long you expect the work to take.",
        }
        widgets = {
            "preferred_start_date": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["preferred_start_date"].widget.attrs["min"] = timezone.localdate().isoformat()

    def clean_preferred_start_date(self):
        value = self.cleaned_data.get("preferred_start_date")
        if value and value < timezone.localdate():
            raise ValidationError("The preferred start date cannot be in the past.")
        return value


class DeclineRequestForm(StyledFormMixin, forms.Form):
    # The same form also submits "accept" and "under review", so the reason is only
    # marked required in the browser when the decline button is used.
    use_required_attribute = False
    reason = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 3}), min_length=10, max_length=1000, label="Reason if declining",
        help_text="Required when declining. The client will see this reason.",
    )


class CancellationForm(StyledFormMixin, forms.Form):
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), min_length=10, max_length=1000, label="Reason for cancelling")


class MessageForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Message
        fields = ["body"]
        labels = {"body": "Message"}
        widgets = {"body": forms.Textarea(attrs={"rows": 3, "placeholder": "Write a message to the company or client…"})}


class ProjectUpdateForm(StyledFormMixin, forms.ModelForm):
    field_attrs = {"progress_percentage": {"min": 0, "max": 100, "step": 1}}

    class Meta:
        model = Project
        fields = ["status", "progress_percentage", "progress_note", "start_date", "expected_end_date"]
        labels = {"progress_percentage": "Progress (%)"}
        help_texts = {"progress_percentage": "Set automatically to 100 when the project is completed."}
        widgets = {
            "progress_note": forms.Textarea(attrs={"rows": 3}),
            "start_date": forms.DateInput(attrs={"type": "date"}),
            "expected_end_date": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["status"].choices = [
            (Project.Status.PLANNED, "Planned"),
            (Project.Status.IN_PROGRESS, "In progress"),
            (Project.Status.ON_HOLD, "On hold"),
            (Project.Status.COMPLETED, "Completed"),
        ]

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("status") == Project.Status.COMPLETED:
            cleaned["progress_percentage"] = 100
        start, end = cleaned.get("start_date"), cleaned.get("expected_end_date")
        if start and end and end < start:
            self.add_error("expected_end_date", "The expected end date cannot be before the start date.")
        return cleaned


class RecommendedWorkerChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, worker):
        suffix = " · Trade match" if getattr(worker, "trade_match", 1) == 0 else ""
        history = getattr(worker, "project_history", 0)
        history_label = f" · {history} prior projects" if history else ""
        return f"{worker.first_name} {worker.last_name} — {worker.primary_trade}, {worker.experience_years} yrs{suffix}{history_label}"


class AssignmentForm(StyledFormMixin, forms.Form):
    worker = RecommendedWorkerChoiceField(queryset=Worker.objects.none(), empty_label="Select an available worker")
    role = forms.CharField(max_length=100, required=False, help_text="Role on this project, such as supervisor or electrician.")

    def __init__(self, *args, company=None, preferred_trade="", project=None, **kwargs):
        super().__init__(*args, **kwargs)
        if company:
            queryset = Worker.objects.filter(company=company, employment_status=Worker.Employment.ACTIVE, availability=Worker.Availability.AVAILABLE)
            trade_match = Case(
                When(primary_trade__iexact=preferred_trade, then=Value(0)),
                When(skills__icontains=preferred_trade, then=Value(0)),
                default=Value(1), output_field=IntegerField(),
            ) if preferred_trade else Value(1, output_field=IntegerField())
            queryset = queryset.annotate(
                active_load=Count("assignments", filter=Q(assignments__status="active")),
                project_history=Count("assignments", filter=Q(assignments__status="completed")),
                trade_match=trade_match,
            ).filter(active_load=0)
            if preferred_trade:
                queryset = queryset.order_by("trade_match", "-experience_years", "-project_history", "last_name", "first_name")
            else:
                queryset = queryset.order_by("-experience_years", "-project_history", "last_name", "first_name")
            if project:
                queryset = queryset.exclude(assignments__project=project)
            self.fields["worker"].queryset = queryset.distinct()


RATING_CHOICES = [(5, "5 — Excellent"), (4, "4 — Good"), (3, "3 — Average"), (2, "2 — Poor"), (1, "1 — Very poor")]


class RatingForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Rating
        fields = ["overall_rating", "quality_rating", "professionalism_rating", "punctuality_rating", "communication_rating", "comment"]
        widgets = {
            "overall_rating": forms.Select(choices=[("", "Select a rating"), *RATING_CHOICES]),
            "quality_rating": forms.Select(choices=[("", "Not rated"), *RATING_CHOICES]),
            "professionalism_rating": forms.Select(choices=[("", "Not rated"), *RATING_CHOICES]),
            "punctuality_rating": forms.Select(choices=[("", "Not rated"), *RATING_CHOICES]),
            "communication_rating": forms.Select(choices=[("", "Not rated"), *RATING_CHOICES]),
            "comment": forms.Textarea(attrs={"rows": 4, "maxlength": 2000}),
        }


class ComplaintForm(StyledFormMixin, forms.ModelForm):
    subject = forms.CharField(max_length=160, min_length=5, widget=forms.TextInput(attrs={"placeholder": "Short summary of the issue"}))
    details = forms.CharField(
        min_length=20, max_length=4000, widget=forms.Textarea(attrs={"rows": 5}),
        help_text="Describe what happened, when it happened, and who was involved.",
    )

    class Meta:
        model = Complaint
        fields = ["subject", "details"]


class ReviewCompanyForm(StyledFormMixin, forms.Form):
    NOTE_REQUIRED = {Company.Status.UNDER_REVIEW, Company.Status.REJECTED, Company.Status.SUSPENDED, Company.Status.DEACTIVATED}

    status = forms.ChoiceField(choices=[
        (Company.Status.PENDING, "Keep pending"),
        (Company.Status.UNDER_REVIEW, "Request correction"),
        (Company.Status.APPROVED, "Approve / reactivate"),
        (Company.Status.REJECTED, "Reject"),
        (Company.Status.SUSPENDED, "Suspend"),
        (Company.Status.DEACTIVATED, "Deactivate"),
    ], label="Decision")
    note = forms.CharField(
        required=False, max_length=2000, widget=forms.Textarea(attrs={"rows": 3}), label="Review note / reason",
        help_text="Required when requesting a correction, rejecting, suspending or deactivating.",
    )

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("status") in self.NOTE_REQUIRED and not cleaned.get("note"):
            self.add_error("note", "Add a note so the company knows what to do next.")
        return cleaned


# ---------------------------------------------------------------------------
# Site operations forms
# ---------------------------------------------------------------------------

DATE_INPUT = {"type": "date"}
DATETIME_INPUT_FORMAT = "%Y-%m-%dT%H:%M"
SLE_ATTRS = {"min": 0, "step": "0.01", "inputmode": "decimal"}


def validate_date_order(form, start_field, end_field, message):
    start, end = form.cleaned_data.get(start_field), form.cleaned_data.get(end_field)
    if start and end and end < start:
        form.add_error(end_field, message)


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleImageField(forms.FileField):
    """Accepts several image uploads at once (up to `max_files`)."""

    def __init__(self, *args, max_files=6, **kwargs):
        self.max_files = max_files
        kwargs.setdefault("widget", MultipleFileInput())
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        files = data if isinstance(data, (list, tuple)) else ([data] if data else [])
        if len(files) > self.max_files:
            raise ValidationError(f"Upload {self.max_files} photos or fewer.")
        cleaned = [super(MultipleImageField, self).clean(item, initial) for item in files]
        for item in cleaned:
            validate_image_upload(item)
        return cleaned


class StaffAccountForm(RegistrationFields):
    """Creates a sign-in account for a project manager, supervisor or worker in the company."""

    role = forms.ChoiceField(label="Account type")
    phone = forms.CharField(max_length=40, required=False, label="Mobile number", help_text="Used for SMS alerts.")
    worker = forms.ModelChoiceField(
        queryset=Worker.objects.none(), required=False, label="Worker record",
        help_text="Required for worker accounts. Links the login to the worker's records, tasks and pay.",
    )

    field_attrs = RegistrationFields.field_attrs
    phone_fields = {"phone": True}
    sections = [
        ("Role", "Project managers run projects. Supervisors run sites and record attendance. Workers see their own tasks and pay.", ["role", "worker"]),
        ("Account details", "The person will use these details to sign in.", ["username", "email", "first_name", "last_name", "phone", "password", "password_confirm"]),
    ]

    def __init__(self, *args, company, creator, **kwargs):
        super().__init__(*args, **kwargs)
        roles = [User.Role.SUPERVISOR, User.Role.WORKER]
        if creator.role == User.Role.COMPANY:
            roles.insert(0, User.Role.MANAGER)
        self.fields["role"].choices = [(role.value, role.label) for role in roles]
        self.fields["worker"].queryset = company.workers.filter(employment_status=Worker.Employment.ACTIVE, user__isnull=True)
        self.fields["worker"].empty_label = "Select the worker's record"

    def clean_phone(self):
        value = self.cleaned_data.get("phone")
        if value and phone_in_use(value, User.objects.exclude(phone="").values_list("phone", flat=True)):
            raise ValidationError("Another account already uses this mobile number.")
        return value

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("role") == User.Role.WORKER and not cleaned.get("worker"):
            self.add_error("worker", "Choose which worker record this account belongs to.")
        if cleaned.get("role") != User.Role.WORKER:
            cleaned["worker"] = None
        return cleaned


class ProjectForm(StyledFormMixin, forms.ModelForm):
    client_email = forms.EmailField(
        required=False, label="Client email",
        help_text="Optional. If the client has a BuildLink account, they get read-only access to progress.",
    )

    field_attrs = {
        "title": {"placeholder": "e.g. Lumley 3-bedroom house"},
        "location": {"placeholder": "e.g. Lumley Beach Road, Freetown"},
        "budget": {**SLE_ATTRS, "placeholder": "e.g. 450000.00"},
        "client_email": {"autocomplete": "off", "placeholder": "client@example.com"},
    }
    sections = [
        ("Project details", "What is being built and where.", ["title", "description", "location", "client_email"]),
        ("Schedule and budget", "Planned dates and the total budget in Leones.", ["start_date", "expected_end_date", "budget"]),
        ("People", "Who runs this project day to day.", ["manager", "supervisors"]),
    ]

    class Meta:
        model = Project
        fields = ["title", "description", "location", "start_date", "expected_end_date", "budget", "manager", "supervisors"]
        labels = {"budget": "Budget (SLE)", "manager": "Project manager", "expected_end_date": "Expected completion"}
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
            "start_date": forms.DateInput(attrs=DATE_INPUT),
            "expected_end_date": forms.DateInput(attrs=DATE_INPUT),
            "supervisors": forms.CheckboxSelectMultiple(),
        }

    def __init__(self, *args, company, **kwargs):
        super().__init__(*args, **kwargs)
        self.company = company
        managers = User.objects.filter(Q(pk=company.owner_id) | Q(employer=company, role=User.Role.MANAGER), is_active=True)
        self.fields["manager"].queryset = managers
        self.fields["manager"].empty_label = "Select a project manager"
        self.fields["supervisors"].queryset = User.objects.filter(employer=company, role=User.Role.SUPERVISOR, is_active=True)
        self.fields["supervisors"].help_text = "Supervisors can record attendance and submit site reports for this project."
        if self.instance.pk and self.instance.client:
            self.fields["client_email"].initial = self.instance.client.email

    def clean_client_email(self):
        email = self.cleaned_data.get("client_email", "").strip().lower()
        if not email:
            return None
        client = User.objects.filter(email__iexact=email, role=User.Role.CLIENT, is_active=True).first()
        if client is None:
            raise ValidationError("No active client account uses this email address.")
        return client

    def clean(self):
        cleaned = super().clean()
        validate_date_order(self, "start_date", "expected_end_date", "The expected completion date cannot be before the start date.")
        return cleaned

    def save(self, commit=True):
        project = super().save(commit=False)
        project.client = self.cleaned_data.get("client_email")
        if commit:
            project.save()
            self.save_m2m()
        return project


class SiteForm(StyledFormMixin, forms.ModelForm):
    field_attrs = {
        "latitude": {"step": "0.000001", "placeholder": "e.g. 8.455500", "inputmode": "decimal"},
        "longitude": {"step": "0.000001", "placeholder": "e.g. -13.260000", "inputmode": "decimal"},
        "radius_m": {"min": 20, "max": 5000},
    }
    sections = [
        ("Site", "A place where work happens for this project.", ["name", "address"]),
        ("Attendance boundary", "Workers can only clock in inside this circle. Use 'Use my location' while standing on site.", ["latitude", "longitude", "radius_m", "enforce_geofence"]),
    ]

    class Meta:
        model = Site
        fields = ["name", "address", "latitude", "longitude", "radius_m", "enforce_geofence"]
        labels = {"radius_m": "Boundary radius (metres)"}

    def clean(self):
        cleaned = super().clean()
        has_lat, has_lng = cleaned.get("latitude") is not None, cleaned.get("longitude") is not None
        if has_lat != has_lng:
            self.add_error("longitude" if has_lat else "latitude", "Enter both latitude and longitude, or neither.")
        elif cleaned.get("enforce_geofence") and not has_lat:
            self.add_error("latitude", "Set the site's coordinates to enforce the attendance boundary, or untick the boundary option.")
        return cleaned


class PhaseForm(StyledFormMixin, forms.ModelForm):
    field_attrs = {"name": {"placeholder": "e.g. Foundation"}, "order": {"min": 1, "max": 99}}

    class Meta:
        model = Phase
        fields = ["name", "order", "start_date", "end_date", "status", "is_milestone"]
        labels = {"order": "Order", "is_milestone": "This is a milestone"}
        widgets = {"start_date": forms.DateInput(attrs=DATE_INPUT), "end_date": forms.DateInput(attrs=DATE_INPUT)}

    def clean(self):
        cleaned = super().clean()
        validate_date_order(self, "start_date", "end_date", "The end date cannot be before the start date.")
        return cleaned


class TaskForm(StyledFormMixin, forms.ModelForm):
    field_attrs = {"title": {"placeholder": "e.g. Lay ground-floor block walls"}, "workers_needed": {"min": 1, "max": 100}}
    sections = [
        ("Task", "What needs doing.", ["title", "description", "phase", "site"]),
        ("Requirements", "Used to suggest the best available workers.", ["required_skill", "workers_needed", "priority"]),
        ("Schedule", "When the work should happen.", ["start_date", "due_date"]),
    ]

    class Meta:
        model = Task
        fields = ["title", "description", "phase", "site", "required_skill", "workers_needed", "priority", "start_date", "due_date"]
        labels = {"required_skill": "Skill needed", "due_date": "Deadline"}
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
            "start_date": forms.DateInput(attrs=DATE_INPUT),
            "due_date": forms.DateInput(attrs=DATE_INPUT),
        }

    def __init__(self, *args, project, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["phase"].queryset = project.phases.all()
        self.fields["site"].queryset = project.sites.all()
        self.fields["phase"].empty_label = "No phase"
        self.fields["site"].empty_label = "No specific site"
        self.fields["required_skill"].empty_label = "Any skill"

    def clean(self):
        cleaned = super().clean()
        validate_date_order(self, "start_date", "due_date", "The deadline cannot be before the start date.")
        return cleaned


class TaskProgressForm(StyledFormMixin, forms.Form):
    progress = forms.IntegerField(min_value=0, max_value=100, label="Progress (%)", help_text="100% marks the task completed.")
    field_attrs = {"progress": {"step": 5}}


class TaskAssignForm(forms.Form):
    worker = forms.ModelChoiceField(queryset=Worker.objects.none())

    def __init__(self, *args, candidates, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["worker"].queryset = Worker.objects.filter(pk__in=[match.worker.pk for match in candidates])


class ManualAttendanceForm(StyledFormMixin, forms.Form):
    worker = forms.ModelChoiceField(queryset=Worker.objects.none(), empty_label="Select a worker")
    note = forms.CharField(max_length=240, min_length=5, label="Reason for manual entry", help_text="For example: phone has no GPS, QR card lost.")

    def __init__(self, *args, workers, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["worker"].queryset = workers


class AbsenceForm(StyledFormMixin, forms.Form):
    worker = forms.ModelChoiceField(queryset=Worker.objects.none(), empty_label="Select a worker")
    date = forms.DateField(widget=forms.DateInput(attrs=DATE_INPUT))
    status = forms.ChoiceField(choices=[(value, label) for value, label in Attendance.Status.choices if value != Attendance.Status.PRESENT])
    note = forms.CharField(max_length=240, required=False, label="Reason")

    def __init__(self, *args, workers, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["worker"].queryset = workers
        self.fields["date"].initial = timezone.localdate()
        self.fields["date"].widget.attrs["max"] = timezone.localdate().isoformat()

    def clean_date(self):
        value = self.cleaned_data["date"]
        if value > timezone.localdate():
            raise ValidationError("Absences can only be recorded for today or earlier.")
        return value


class SiteReportForm(StyledFormMixin, forms.ModelForm):
    photos = MultipleImageField(required=False, label="Site photos", help_text="Up to 6 images · PNG, JPG or WEBP · 5 MB each.")
    field_attrs = {
        "summary": {"placeholder": "e.g. Ground-floor walls 60% complete"},
        "task_progress": {"min": 0, "max": 100, "step": 5},
        "workers_on_site": {"min": 0, "max": 500},
        "photos": {**IMAGE_UPLOAD_ATTRS, "multiple": True},
    }
    sections = [
        ("Today on site", "A short record of the day's work.", ["date", "site", "weather", "workers_on_site", "summary", "work_completed", "issues"]),
        ("Task progress", "Optionally update one task's completion.", ["task", "task_progress"]),
        ("Photos", "Evidence of progress or problems.", ["photos"]),
    ]

    class Meta:
        model = SiteReport
        fields = ["date", "site", "weather", "workers_on_site", "summary", "work_completed", "issues", "task", "task_progress"]
        labels = {"task_progress": "New task progress (%)", "workers_on_site": "Workers on site"}
        widgets = {
            "date": forms.DateInput(attrs=DATE_INPUT),
            "work_completed": forms.Textarea(attrs={"rows": 4}),
            "issues": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, project, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["site"].queryset = project.sites.all()
        self.fields["site"].empty_label = "Whole project"
        self.fields["task"].queryset = project.tasks.exclude(status=Task.Status.COMPLETED)
        self.fields["task"].empty_label = "No task update"
        self.fields["date"].widget.attrs["max"] = timezone.localdate().isoformat()

    def clean_date(self):
        value = self.cleaned_data["date"]
        if value > timezone.localdate():
            raise ValidationError("Reports cannot be dated in the future.")
        return value

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("task_progress") is not None and not cleaned.get("task"):
            self.add_error("task", "Choose the task this progress applies to.")
        if cleaned.get("task") and cleaned.get("task_progress") is None:
            self.add_error("task_progress", "Enter the task's new progress.")
        return cleaned


class IncidentForm(StyledFormMixin, forms.ModelForm):
    ppe_compliant = forms.NullBooleanField(
        required=False, label="Was PPE worn correctly?",
        widget=forms.Select(choices=[("unknown", "Not applicable / unknown"), ("true", "Yes"), ("false", "No")]),
    )
    sections = [
        ("What happened", "Report every accident, near miss and safety breach, however small.", ["kind", "severity", "occurred_at", "site", "description"]),
        ("People and PPE", "Who was involved and whether protective equipment was worn.", ["workers_involved", "ppe_compliant"]),
        ("Response", "Immediate action taken and any photo evidence.", ["action_taken", "photo"]),
    ]
    field_attrs = {"photo": IMAGE_UPLOAD_ATTRS}

    class Meta:
        model = Incident
        fields = ["kind", "severity", "occurred_at", "site", "description", "workers_involved", "ppe_compliant", "action_taken", "photo"]
        labels = {"occurred_at": "Date and time", "action_taken": "Action taken"}
        widgets = {
            "occurred_at": forms.DateTimeInput(attrs={"type": "datetime-local"}, format=DATETIME_INPUT_FORMAT),
            "description": forms.Textarea(attrs={"rows": 4}),
            "action_taken": forms.Textarea(attrs={"rows": 3}),
            "workers_involved": forms.CheckboxSelectMultiple(),
            "severity": forms.RadioSelect(attrs={"class": "severity-picker"}),
        }

    def __init__(self, *args, project, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["site"].queryset = project.sites.all()
        self.fields["site"].empty_label = "Not site-specific"
        self.fields["kind"].choices = [("", "Select the type of incident"), *Incident.Kind.choices]
        self.fields["severity"].choices = Incident.Severity.choices
        self.fields["workers_involved"].queryset = project.company.workers.filter(assignments__project=project).distinct()
        self.fields["occurred_at"].input_formats = [DATETIME_INPUT_FORMAT]
        self.fields["occurred_at"].widget.attrs["max"] = timezone.localtime().strftime(DATETIME_INPUT_FORMAT)

    def clean_occurred_at(self):
        value = self.cleaned_data["occurred_at"]
        if value > timezone.now():
            raise ValidationError("The incident time cannot be in the future.")
        return value


class IncidentUpdateForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Incident
        fields = ["status", "action_taken"]
        widgets = {"action_taken": forms.Textarea(attrs={"rows": 3})}

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("status") == Incident.Status.CLOSED and not cleaned.get("action_taken"):
            self.add_error("action_taken", "Record the action taken before closing the incident.")
        return cleaned


class MaterialRequestForm(StyledFormMixin, forms.ModelForm):
    field_attrs = {
        "item": {"placeholder": "e.g. Cement (50 kg bags)"},
        "quantity": {"min": "0.01", "step": "0.01", "inputmode": "decimal"},
        "unit": {"placeholder": "e.g. bags"},
        "estimated_cost": {**SLE_ATTRS, "placeholder": "e.g. 12500.00"},
    }
    sections = [
        ("Request", "What is needed on site.", ["kind", "item", "quantity", "unit", "site", "needed_by"]),
        ("Justification", "Helps the project manager approve quickly.", ["estimated_cost", "reason"]),
    ]

    class Meta:
        model = MaterialRequest
        fields = ["kind", "item", "quantity", "unit", "site", "needed_by", "estimated_cost", "reason"]
        labels = {"estimated_cost": "Estimated cost (SLE)", "needed_by": "Needed by"}
        widgets = {"needed_by": forms.DateInput(attrs=DATE_INPUT), "reason": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, project, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["site"].queryset = project.sites.all()
        self.fields["site"].empty_label = "Any site"
        self.fields["needed_by"].widget.attrs["min"] = timezone.localdate().isoformat()

    def clean_needed_by(self):
        value = self.cleaned_data.get("needed_by")
        if value and value < timezone.localdate():
            raise ValidationError("The needed-by date cannot be in the past.")
        return value


class MaterialReviewForm(StyledFormMixin, forms.Form):
    decision = forms.ChoiceField(choices=[
        (MaterialRequest.Status.APPROVED, "Approve"),
        (MaterialRequest.Status.REJECTED, "Reject"),
        (MaterialRequest.Status.DELIVERED, "Mark delivered"),
    ])
    note = forms.CharField(required=False, max_length=1000, widget=forms.Textarea(attrs={"rows": 2}), label="Note to the requester")

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("decision") == MaterialRequest.Status.REJECTED and not cleaned.get("note"):
            self.add_error("note", "Explain why the request is rejected.")
        return cleaned


class PayrollPeriodForm(StyledFormMixin, forms.Form):
    period_start = forms.DateField(widget=forms.DateInput(attrs=DATE_INPUT), label="Period start")
    period_end = forms.DateField(widget=forms.DateInput(attrs=DATE_INPUT), label="Period end")

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("period_start"), cleaned.get("period_end")
        if start and end:
            if end < start:
                self.add_error("period_end", "The period end must be on or after the start.")
            elif (end - start).days > 31:
                self.add_error("period_end", "A pay period can be at most one month long.")
        return cleaned


class PayrollAdjustForm(StyledFormMixin, forms.Form):
    deductions = forms.DecimalField(min_value=0, max_digits=12, decimal_places=2, label="Deductions (SLE)", help_text="Advances, fines or other deductions.")
    field_attrs = {"deductions": SLE_ATTRS}

    def __init__(self, *args, record, **kwargs):
        super().__init__(*args, **kwargs)
        self.record = record
        self.fields["deductions"].initial = record.deductions

    def clean_deductions(self):
        value = self.cleaned_data["deductions"]
        if value > self.record.gross_pay:
            raise ValidationError(f"Deductions cannot exceed gross pay (SLE {self.record.gross_pay:,.2f}).")
        return value


class AnnouncementForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Announcement
        fields = ["project", "title", "body", "send_sms", "send_email"]
        labels = {"project": "Audience", "body": "Message"}
        widgets = {"body": forms.Textarea(attrs={"rows": 4, "maxlength": 1000})}

    def __init__(self, *args, projects, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["project"].queryset = projects
        self.fields["project"].empty_label = "Everyone in the company"
        self.fields["project"].help_text = "Choose a project to reach only its team."


# ---------------------------------------------------------------------------
# Sign-in
# ---------------------------------------------------------------------------

def mask_email(email):
    """ma•••@gmail.com: enough to recognise your own address without exposing it."""
    name, _, domain = email.partition("@")
    return f"{name[:2]}{'•' * 3}@{domain}"


class VerifiedLoginForm(AuthenticationForm):
    """Signs in only accounts with a confirmed email address.

    The check runs after the password is accepted, so it never reveals whether an
    email or username is registered to someone who does not know the password.
    """

    error_messages = {
        **AuthenticationForm.error_messages,
        "unverified": "Confirm your email address before signing in. We sent a link to %(email)s.",
    }
    unverified_user = None

    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)
        if not user.email_verified:
            self.unverified_user = user
            raise ValidationError(self.error_messages["unverified"], code="unverified", params={"email": mask_email(user.email)})
