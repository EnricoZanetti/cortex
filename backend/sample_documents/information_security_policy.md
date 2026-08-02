# Information Security Policy

Northgate Financial Services — Internal Policy NGF-SEC-001
Version 5.0 · Owner: Chief Information Security Officer

## 1. Scope

This policy applies to all information assets owned or processed by the firm, and to
everyone who accesses them: employees, contractors, and third parties acting on our
behalf. It covers firm-owned and approved personal devices.

## 2. Data classification

Every document and dataset carries one of four classifications. When in doubt, classify
upward and ask the information owner.

- **Public** — approved for external publication. No restrictions.
- **Internal** — default for firm material. May be shared inside the firm; not outside.
- **Confidential** — client data, financial results before release, personal data.
  Sharing outside the firm requires a signed non-disclosure agreement and information
  owner approval.
- **Restricted** — material non-public information, credentials, security architecture,
  investigation files. Access is granted individually and reviewed monthly.

Confidential and Restricted material must be encrypted at rest and in transit, and may
never be placed in personal cloud storage, personal email, or unapproved AI tools.

## 3. Access control

Access follows least privilege: users receive the minimum access needed for their role,
granted through role-based entitlements rather than individual grants wherever possible.

Access reviews are run quarterly by system owners, and immediately on any role change.
Leavers are deprovisioned at 18:00 on their last working day; the joiner-mover-leaver
process is audited monthly.

Privileged and administrative accounts are separate from day-to-day accounts, require a
hardware security key, and their sessions are recorded.

## 4. Authentication

Passwords must be at least 14 characters and must not be reused across services. The firm
provides a password manager; storing credentials in spreadsheets, documents or browsers is
prohibited.

Multi-factor authentication is mandatory for all remote access and for all administrative
functions. SMS is not an accepted second factor. Anyone with production access must
enrol a hardware security key.

## 5. Devices and endpoints

Firm devices run the managed endpoint agent, full-disk encryption and automatic patching.
Personal devices may access email and calendar only, through the managed application, and
only after enrolment.

Report a lost or stolen device to the IT service desk immediately and in any case within
two hours of noticing, so the device can be wiped remotely.

Software is installed only from the firm's approved catalogue. Requests for other software
go through the IT service desk and are assessed by Security before approval.

## 6. Email, messaging and AI tools

Treat email and chat as discoverable records. Do not send Confidential or Restricted
material to personal addresses, and use the secure file-transfer service rather than
attachments when sending Confidential data to approved external parties.

Only AI assistants approved by Security may be used with firm data, and only up to
Confidential classification where the tool is covered by a firm agreement. Restricted
material must never be entered into any AI tool.

## 7. Incident reporting

Report any suspected security incident — phishing, malware, data sent to the wrong
recipient, lost device, suspected account compromise — to security@northgate.example
**within one hour** of noticing it.

Do not attempt to investigate or remediate an incident yourself. Preserve evidence: do not
delete the suspicious message, and do not power off a suspect machine unless Security asks
you to.

There is no penalty for reporting an incident you caused. There is a disciplinary process
for failing to report one.

## 8. Third parties

Any supplier processing firm or client data is assessed by Security before contracting and
reassessed annually, or on any material change to the service. Contracts must include
audit rights, breach notification within 24 hours, and defined data-deletion obligations
at termination.

## 9. Business continuity

Critical systems are backed up daily with a recovery point objective of four hours and a
recovery time objective of eight hours. Restore tests are performed quarterly and the
results reported to the Technology Risk Committee.

## 10. Compliance with this policy

Compliance is monitored through automated controls, quarterly access reviews and internal
audit. Exceptions require a documented risk acceptance approved by the CISO, with an
expiry date of no more than twelve months.
