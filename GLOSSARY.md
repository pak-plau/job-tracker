# Job Tracker

Tracks a person's job applications by reading the emails companies send them. Each person runs their own private copy for their own mailbox.

## Language

### Applications

**Application**:
One attempt by the person to get one Role at one Company. Reapplying later is a new Application.
_Avoid_: Job, submission, candidacy

**Company**:
The employer being applied to, not the hiring system that sent the email.
_Avoid_: Employer, sender, organization

**Role**:
The position applied for. It may be unknown when no email has named it.
_Avoid_: Position, job title, opening

**Status**:
An Application's current stage: Applied, Interviewing, Rejected or Offer.
_Avoid_: Stage, state, outcome

**Applied**:
The Company has received the Application and has not yet invited any next step.
_Avoid_: Submitted, pending

**Interviewing**:
The Company has invited a next step beyond the Application, including recruiter screens and online assessments.
_Avoid_: Screening, in progress

**Rejected**:
The Company has declined to move forward with the Application.
_Avoid_: Declined, closed

**Offer**:
The Company has offered the Role.
_Avoid_: Accepted, hired

**Needs Reply**:
A marker on an Application with an Information Request the person has not yet cleared. It is separate from Status.
_Avoid_: Action needed, flagged, pending

**Change Log**:
The permanent record of every change to an Application: what changed, when, and whether the tracker or the person made it.
_Avoid_: Audit trail, history, activity feed

### Emails

**Confirmation Email**:
An email from a Company acknowledging that it received an Application.
_Avoid_: Receipt, acknowledgement

**Interview Invitation**:
An email inviting a next step that makes an Application Interviewing. Reminders and reschedules are not Interview Invitations.
_Avoid_: Scheduling email, interview email

**Information Request**:
An email asking the person to provide missing information or documents, or finish an incomplete form, so an Application can proceed. Interview availability requests and assessments are Interview Invitations instead.
_Avoid_: Action email, follow-up

**Rejection Email**:
An email telling the person that an Application is Rejected.
_Avoid_: Decline email

**Offer Email**:
An email telling the person that an Application has reached Offer.
_Avoid_: Acceptance email

**Unclear Email**:
A job-related email whose meaning cannot be determined with confidence.
_Avoid_: Unknown email, low-confidence email

**Job Alert**:
A newsletter, alert or recommendation of open roles the person could apply to. It is not about an Application they already made, so it never creates or changes an Application and never goes on the Review List.
_Avoid_: Job digest, job newsletter

**Review List**:
The emails the tracker refuses to decide on, waiting for the person to resolve them: Unclear Emails, emails that could match more than one Application, Interview Invitations, Information Requests, Rejection Emails or Offer Emails with no matching Application, and emails that contradict a final Status.
_Avoid_: Queue, inbox, triage
