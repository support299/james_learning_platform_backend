"""Group names for onboarding-only access.

These are Django auth Groups, not a rewrite of Academy staff/student roles.
LMS behaviour still keys off User.is_staff.
"""

GROUP_ASSISTANT = 'Onboarding Assistant'
GROUP_RECRUITER = 'Onboarding Recruiter'
GROUP_LEADERSHIP = 'Onboarding Leadership'

ROLE_ASSISTANT = 'assistant'
ROLE_RECRUITER = 'recruiter'
ROLE_LEADERSHIP = 'leadership'

ONBOARDING_GROUPS = (
    GROUP_ASSISTANT,
    GROUP_RECRUITER,
    GROUP_LEADERSHIP,
)
