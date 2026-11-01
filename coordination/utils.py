from .models import AuditLog, Notification, User


def client_ip(request):
    return request.META.get("REMOTE_ADDR")


def log_event(request, action, module, description, record_id=""):
    AuditLog.objects.create(
        user=request.user if request.user.is_authenticated else None,
        action=action,
        module=module,
        record_id=str(record_id),
        description=description,
        ip_address=client_ip(request),
    )


def notify(user, title, message, link=""):
    if user:
        Notification.objects.create(user=user, title=title, message=message, link=link)


def notify_admins(title, message, link=""):
    for admin in User.objects.filter(role=User.Role.ADMIN, is_active=True):
        notify(admin, title, message, link)
