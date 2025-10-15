from django import template

register = template.Library()


@register.filter(name="starts_with")
def starts_with(value: str, prefix: str) -> bool:
    """Check if the value starts with the given prefix."""
    if value is None:
        return False
    return str(value).startswith(prefix)
