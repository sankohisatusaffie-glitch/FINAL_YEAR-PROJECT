from django.contrib import admin

from .models import (
    Announcement, Attendance, AuditLog, ClientProfile, Company, Complaint, Incident, MaterialRequest,
    Message, MobileMoneyTransaction, Notification, OutboundMessage, PayrollRecord, Phase, Project,
    Rating, ReportPhoto, Service, ServiceRequest, Site, SiteReport, Skill, Task, TaskAssignment,
    User, Worker, WorkerAssignment,
)


class OversightAdmin(admin.ModelAdmin):
    """Keep the Django admin useful for review without bypassing portal workflows."""

    def get_readonly_fields(self, request, obj=None):
        fields = list(self.model._meta.fields) + list(self.model._meta.many_to_many)
        return [field.name for field in fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return False


class ServiceAdmin(admin.ModelAdmin):
    list_display = ["name", "active"]
    list_filter = ["active"]
    search_fields = ["name"]


class SkillAdmin(admin.ModelAdmin):
    list_display = ["name", "category"]
    search_fields = ["name", "category"]


admin.site.register(Service, ServiceAdmin)
admin.site.register(Skill, SkillAdmin)
for model in [
    User, Company, ClientProfile, Worker, ServiceRequest, Project,
    WorkerAssignment, Rating, Message, Notification, AuditLog, Complaint,
    Site, Phase, Task, TaskAssignment, Attendance, SiteReport, ReportPhoto, Incident,
    MaterialRequest, PayrollRecord, MobileMoneyTransaction, Announcement, OutboundMessage,
]:
    admin.site.register(model, OversightAdmin)

admin.site.site_header = "Freetown Workforce Coordination"
admin.site.site_title = "Coordination administration"
admin.site.index_title = "Read only platform records"
