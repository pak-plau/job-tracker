"""Decide what a classified email does to the person's Applications. Terms are defined in GLOSSARY.md.

decide() only returns a decision. Saving it is the database's job.
"""
from job_tracker.classify import normalize_company

OPEN_STATUSES = ("Applied", "Interviewing")
STATUS_FOR_CATEGORY = {"interview": "Interviewing", "rejection": "Rejected", "offer": "Offer"}


def decide(category, company, role, applications):
    """Return what to do, given the Applications the person hasn't removed. See the "action" key:

    discard, review (reason), create (company, role), or, for an existing application_id with a
    role to fill in if it was unknown: update (status, needs_reply), log, or ignore.
    """
    if category in ("job_alert", "irrelevant"):
        return {"action": "discard"}
    if category == "unclear":
        return _review("The model couldn't tell what this email means.")
    normalized = normalize_company(company)
    if not normalized:
        return _review("The email doesn't say which company it's about.")

    at_company = [a for a in applications if normalize_company(a["company"]) == normalized]
    open_matches = _matching(role, [a for a in at_company if a["status"] in OPEN_STATUSES])
    final_matches = _matching(role, [a for a in at_company if a["status"] not in OPEN_STATUSES])

    if len(open_matches) > 1:
        return _review("This could be about more than one open application.")
    if category == "confirmation":
        return _on_confirmation(company, role, open_matches, final_matches)
    if open_matches:
        return _on_open(category, role, open_matches[0])
    if not final_matches:
        return _review("No application matches this email.")
    if len(final_matches) > 1:
        return _review("This could be about more than one application.")
    return _on_final(category, role, final_matches[0])


def _matching(role, applications):
    if not role:
        return applications
    named = [a for a in applications if _same_role(a["role"], role)]
    return named or [a for a in applications if not a["role"]]


def _same_role(a, b):
    return (a or "").strip().casefold() == b.strip().casefold()


def _on_confirmation(company, role, open_matches, final_matches):
    if open_matches:
        return _on_existing("ignore", role, open_matches[0])
    # Only a Rejected application counts as a reapplication. A confirmation after an Offer makes no sense.
    if any(a["status"] != "Rejected" for a in final_matches):
        return _review("This confirms an application that has already reached Offer.")
    return {"action": "create", "company": company, "role": role}


def _on_open(category, role, application):
    if category == "info_request":
        return {**_on_existing("update", role, application), "status": None, "needs_reply": True}
    status = STATUS_FOR_CATEGORY[category]
    if status == application["status"]:
        return _on_existing("log", role, application)
    return {**_on_existing("update", role, application), "status": status, "needs_reply": False}


def _on_final(category, role, application):
    if category != "info_request" and STATUS_FOR_CATEGORY[category] == application["status"]:
        return _on_existing("log", role, application)
    return _review(f"This contradicts an application that is already {application['status']}.")


def _on_existing(action, role, application):
    return {"action": action, "application_id": application["id"], "role": role if not application["role"] else None}


def _review(reason):
    return {"action": "review", "reason": reason}
