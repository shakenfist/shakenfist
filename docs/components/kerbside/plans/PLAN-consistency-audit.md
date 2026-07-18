# Consistency Audit Deferred Work

Tracking items from the PROJECT-CONSISTENCY-AUDITS.md review that
require manual action or are pre-existing issues.

## Manual GitHub UI changes needed

These settings must be changed in the GitHub web interface:

### Security settings (Settings > Code security and analysis)

- [x] Enable Dependabot security updates (verified enabled via the
      GitHub API, 2026-07-18)
- [x] Enable Secret scanning (verified enabled via the GitHub API,
      2026-07-18)
- [x] Enable Secret scanning push protection (verified enabled via
      the GitHub API, 2026-07-18)

### Repository settings (Settings > General)

- [ ] Confirm "Delete branch on merge" is enabled
- [ ] Confirm "Allow auto-merge" is enabled

URL: https://github.com/shakenfist/kerbside/settings/security_analysis
