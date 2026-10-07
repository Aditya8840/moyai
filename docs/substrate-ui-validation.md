# Sandbox provider UI verification

These are real browser captures of the deterministic localhost preview, using synthetic organization, endpoint, and credential data. They do not prove a live cloud connection. The baseline is commit `11bf7e7`; both versions use the same preview fixture and viewport sizes.

| Viewport | Before | After |
| --- | --- | --- |
| 1440 × 1000 | [Modal-only runtime](assets/substrate/before-1440.png) | [Provider connection form](assets/substrate/after-1440.png) |
| 768 × 1000 | [Before](assets/substrate/before-768.png) | [After](assets/substrate/after-768.png) |
| 320 × 900 | [Before](assets/substrate/before-320.png) | [After](assets/substrate/after-320.png) |

Checked in the browser:

- Modal/Substrate selection, relevant fields, template guidance, synthetic save, and masked saved token.
- Keyboard Tab order and visible focus; successful save returns focus to Connect and use.
- No document overflow at the three widths.
- [Member view](assets/substrate/member-768.png) explains the selected provider without editable credentials.
- [Error view](assets/substrate/error-768.png) provides a working retry action.
- The workspace front page retains its existing layout.

The existing reduced-motion styles are unchanged; reduced-motion emulation was not available in the browser driver. These checks are not a complete accessibility audit. Backend authorization, CSRF, credential encryption, connection failure, and revision conflict behavior have separate Python tests.

Reproduce with `node scripts/settings_ui_preview.cjs --port 8840`, then open `/#runtime`. Use `/?fixture=member#runtime` and `/?fixture=error#runtime` for alternate states. Writes in this preview are synthetic and do not contact a provider.
