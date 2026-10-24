# BuildDrop iOS IPA Builder

This isolated branch powers a private owner-controlled iOS build flow.

- ZIP upload is kept temporarily in Cloudflare R2.
- A tiny job file is pushed to this branch to trigger GitHub Actions.
- GitHub runs on an ephemeral macOS runner with no repository secrets.
- GitHub Actions authenticates back to BuildDrop with OIDC.
- The output is an unsigned IPA ready for owner-side signing.
- Source is removed after success or failure.

Supported: native Xcode/XcodeGen, Flutter iOS, and React Native iOS projects.
