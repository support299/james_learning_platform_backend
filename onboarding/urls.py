from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    AgentViewSet,
    AuditView,
    CarrierRequirementView,
    CarrierViewSet,
    ChecklistDefinitionViewSet,
    ChecklistItemView,
    CohortViewSet,
    DashboardView,
    RequirementTemplateViewSet,
    SettingsView,
    StaffListView,
    SyncView,
)

router = DefaultRouter()
router.register('cohorts', CohortViewSet, basename='onboarding-cohort')
router.register('agents', AgentViewSet, basename='onboarding-agent')
router.register('carriers', CarrierViewSet, basename='onboarding-carrier')
router.register(
    'checklist-definitions',
    ChecklistDefinitionViewSet,
    basename='onboarding-checklist-definition',
)
router.register(
    'templates',
    RequirementTemplateViewSet,
    basename='onboarding-template',
)

urlpatterns = router.urls + [
    path('dashboard/', DashboardView.as_view(), name='onboarding-dashboard'),
    path('audit/', AuditView.as_view(), name='onboarding-audit'),
    path('settings/', SettingsView.as_view(), name='onboarding-settings'),
    path('sync/', SyncView.as_view(), name='onboarding-sync'),
    path('staff/', StaffListView.as_view(), name='onboarding-staff'),
    path(
        'agents/<int:agent_id>/checklist/<int:item_id>/',
        ChecklistItemView.as_view(),
        name='onboarding-checklist-item',
    ),
    path(
        'agents/<int:agent_id>/carriers/<int:req_id>/',
        CarrierRequirementView.as_view(),
        name='onboarding-carrier-requirement',
    ),
]
