# 04 — UI/UX Design Brief: Visual & Interaction Guide

**CCTV-AI-Agent | MVP v1 | 26 November 2026**  
Design direction is a recommended implementation choice based on the user's request for a polished, interactive interface and the comparison PDF. Keep visual complexity low. Design for an evidence reviewer, not a sci-fi surveillance control room.

## 1. Aesthetic

Dark-first, refined professional dashboard, with quiet contrast, precise typography, generous spacing, small line icons, and focused cyan/teal actions. Inspiration: the restrained information density of Linear and Vercel dashboards, not literal copies. The hackathon presentation's neon cyan/lime is a branding reference only: use restrained teal in the app and lime sparingly for a successful status. Avoid glowing borders on every card, oversized gradients, animated maps, fake graphs, decorative terminal text and stock evidence images. One primary CTA per view. All displayed metrics must come from data.

## 2. Design tokens

| Token | Value / use |
|---|---|
| Canvas | `#0B111C` dark navy |
| Sidebar | `#101A29` |
| Surface | `#142133` cards and forms |
| Raised surface | `#1B2A3D` hover/drawer |
| Border | `#33465A` subtle 1px |
| Primary text | `#F1F5F9` |
| Secondary text | `#A8B6C9` |
| Primary CTA | `#5EE0CA` with `#082D2D` dark text |
| Focus | `#78E5DC` 2px visible outline |
| Success | `#5EE0AD` plus icon/label |
| Warning | `#F7BF62` plus icon/label |
| Error | `#FF9CA9` plus icon/label |
| Optional brand accent | `#C8FF28` limited to small brand detail, never sole status encoding |

Typography: Inter if locally available/bundled, fallback system sans; monospaced system font for IDs/hash only. Page title 28–32px semibold, section 18–20px, card title 15–16px, body 14–16px, caption 12–13px; line height 1.45–1.6. Tabular digits for counts/time. Avoid tiny labels and all-uppercase long text. Spacing base 4px; common 8/12/16/24/32. Cards radius 10–12px; buttons/inputs 8px; chips rounded. Subtle shadow on floating drawer/modal only. Consistent hover/focus/disabled states. Dark mode primary; light theme not required. Test actual contrast rather than assuming token combinations pass.

## 3. Layout system

Desktop (>=1024px): fixed/compact ~224px sidebar; content area max-width ~1440px with 24–32px padding; page header and CTA; grid 2–3 columns for summary cards, responsive findings. Tablet 768–1023px: narrower sidebar or drawer, 2-column cards. Mobile <768px: drawer, 16px padding, single-column cards, large tap targets (44px preferred), no horizontal page scroll. Tables become stacked rows or scroll within contained region. Video/evidence keeps aspect ratio and uses contain instead of cropping critical evidence. Drawer full width on narrow screens. No dependency on hover.

## 4. Screen-by-screen composition

| Screen | Layout | Interaction |
|---|---|---|
| Login | Centered card, simple product name, subtitle, email/password and sign in | Password visibility optional; forgot password; generic auth error; no unnecessary marketing hero. |
| Dashboard | Title, New investigation CTA, 3 small counts (cases, active cases, pending findings), active/recent case list | Counts calculated from owner data; click row opens case; empty state CTA. |
| New case | Step labels “Describe / Add footage / Start”; form followed by drop zone and selected file list | Show per-file name, bytes, status, optional source/time; retry/remove controls; Start disabled with reason. |
| Case workspace | Header with status, job count/progress; tabs Activity / Footage / Findings; cards or rows | Live connection status, actual worker and source status, filter/findings count, real errors; no jurisdiction map. |
| Candidate details | Right drawer or dialog with full frame, source/offset, score and review history | Accept relevance / Reject, confirm with note, save status and errors. Optional retained source playback. |
| History | Search/filter, status chips, timestamps and counts, row click | Restore same case workspace; no redundant investigation duplication. |
| Reset / recovery | Same form styling as login | Accessible success/error states, return to sign in. |

## 5. Wireframes (structure, not sample results)

```text
DESKTOP DASHBOARD
┌────────────────┬─────────────────────────────────────────────────┐
│ CCTV AI Agent  │ Overview                         New investigation│
│ Overview       │ [Cases]       [Active]       [Pending review]    │
│ New case       │ Active investigations                            │
│ History        │ [case / terminal jobs / real status / open]       │
│                │ Recent investigations                            │
│ Account        │ [case / created / findings / status / open]       │
└────────────────┴─────────────────────────────────────────────────┘

NEW CASE                         WORKSPACE
[Title optional]                 [Case title] [status] [live state]
[Description                 ]   [Finished / total jobs][failed count]
[Authorised footage checkbox]    Activity | Footage | Findings
[Drop files / browse         ]   [Worker / source / actual job status]
[File / size / ready / remove]    [Frame] [label] [offset] [review]
[File / error / retry / remove]   [Frame] [label] [offset] [review]
[Start Investigation]             [Inspect details drawer]
```

## 6. Component requirements

Use shared components rather than duplicate styling: AppShell, RouteGuard, PageHeader, StatusChip, MetricCard, EmptyState, ErrorBanner, LoadingSkeleton, FileDropzone, UploadItem, JobRow, ProgressSummary, CandidateCard, EvidenceViewer, ReviewDialog and HistoryRow. These are proposed names; preserve working abstractions when appropriate. Use semantic buttons/links/labels and visible focus. Color plus textual state, e.g. “Retrying”, not only amber dot. Real progress meter with numerator/denominator and accessible aria values; indeterminate when unknown. No per-agent percentage unless backend measures it. Animations 120–180ms for hover/drawer, reduced-motion support, no flashing or infinite fake analysis effects. Live status announcements polite and batched to avoid screen-reader spam. Evidence image alt text describes candidate category/source without asserting identity; use original frame and avoid distortion. Show detector confidence as model score, not guilt/identity probability. Never invent recording location/timestamp.

Forms: explicit validation messages near fields, upload limits before selection, keyboard file chooser, retry/remove affordances, sensible tab order, password autocomplete. Dialogs trap focus, Escape closes if safe, restore focus. All action errors retain user input where possible. Acknowledge storage/review success only after server confirms. No destructive action without confirmation. Show logout near account label; clear cached data.

## 7. Responsive and accessibility quality gates

Check 1440px desktop, 1024px laptop, 768px tablet, 375px mobile and 200% zoom. Verify WCAG AA contrast target (4.5:1 normal text, 3:1 large text and important controls), keyboard navigation, screen-reader labels, visible focus, readable error messages, native video controls and no inaccessible icon-only buttons. Test real empty, loading, error, partial, completed and zero-result states, not just a populated screenshot. Capture screenshots of each main screen with synthetic fixture data after implementation. User should recognize the full process without a tutorial. Do not sacrifice correct evidence access or auth for visual polish.
