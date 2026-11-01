from django import template
from django.core.exceptions import NON_FIELD_ERRORS

register = template.Library()

WIDE_FIELDS = {"address", "description", "skills"}


@register.filter
def wide_field(bound_field):
    """Fields that should span both columns of a two-column field grid.

    A form can set ``wide_fields`` to replace the default set of wide text inputs.
    """
    wide = getattr(bound_field.form, "wide_fields", WIDE_FIELDS)
    return bound_field.use_fieldset or bound_field.widget_type == "textarea" or bound_field.name in wide


@register.filter
def has_field_errors(form):
    return any(name != NON_FIELD_ERRORS for name in form.errors)


@register.simple_tag
def form_sections(form):
    """Group a form's bound fields using its ``sections`` attribute.

    Fields not named in any section are added to the last one, so none are dropped.
    """
    sections = [
        {"title": title, "description": description, "fields": [form[name] for name in names if name in form.fields]}
        for title, description, names in getattr(form, "sections", [])
    ] or [{"title": None, "description": None, "fields": []}]
    listed = {field.name for section in sections for field in section["fields"]}
    sections[-1]["fields"].extend(form[name] for name in form.fields if name not in listed)
    return [section for section in sections if section["fields"]]


@register.filter
def person_name(user, fallback="—"):
    """A user's full name, or username, or `fallback` when there is no user."""
    if not user:
        return fallback
    return user.get_full_name() or user.username


@register.filter
def compact_sle(value):
    """Leones in compact form for stat tiles, for example SLE 12.4K."""
    from coordination.charts import compact

    return f"SLE {compact(value or 0)}"


@register.simple_tag
def icon(name, extra_class=""):
    """An icon from the construction sprite (templates/coordination/_icons.html)."""
    from django.utils.html import format_html

    return format_html('<svg class="icon {}" aria-hidden="true" focusable="false"><use href="#i-{}"></use></svg>', extra_class, name)


TRADE_ICONS = [
    ("electric", "electrician"), ("plumb", "plumber"), ("weld", "welder"), ("metal", "welder"), ("paint", "painter"),
    ("decor", "painter"), ("tile", "tiler"), ("tiling", "tiler"), ("steel", "steel"), ("rebar", "steel"), ("roof", "roofer"),
    ("plaster", "mason"), ("mason", "mason"), ("brick", "mason"), ("block", "mason"), ("carpent", "carpenter"),
    ("capent", "carpenter"), ("joiner", "carpenter"), ("concrete", "concrete"), ("equipment", "excavator"),
    ("excavat", "excavator"), ("operator", "excavator"), ("survey", "survey"), ("hvac", "hvac"), ("air-con", "hvac"),
    ("glaz", "glazier"), ("glass", "glazier"), ("landscap", "landscaper"), ("demolition", "demolition"),
    ("supervisor", "supervisor"), ("foreman", "supervisor"), ("labour", "labourer"), ("labor", "labourer"),
]


@register.filter
def trade_icon(trade):
    """The trade icon name for a worker's trade, matched loosely so legacy spellings still work."""
    value = (trade or "").casefold()
    return next((name for keyword, name in TRADE_ICONS if keyword in value), "labourer")


@register.inclusion_tag("coordination/_trade_badge.html")
def trade_badge(trade):
    return {"trade": trade, "trade_icon": trade_icon(trade)}
