# Publishing and repository maintenance

This guide is for the maintainer publishing the source repository. Application
setup belongs in the [README](../README.md).

## First publication

1. Install [GitHub CLI](https://cli.github.com/) and authenticate in the same
   environment used for Git:

   ```bash
   gh auth login --hostname github.com --git-protocol https --web
   gh auth status
   ```

   Use GitHub's browser/device flow. The repository name used below is
   `clipforge`; choose another name if it is already used in your account.

2. From the repository root, inspect `git status` and the files eligible for
   publication:

   ```bash
   git status --short
   git ls-files --cached --others --exclude-standard
   ```

   Check the source/docs for credentials and private data. Environments,
   generated builds/reports, local settings, and application data are ignored.
   Keep personal development archives outside the repository.

3. Run the checks in [Contributing](../CONTRIBUTING.md). Configure your preferred
   Git author identity yourself if it is not already configured. Create an
   initial commit containing only the reviewed public files, and use `main` as
   the default branch.

4. Once the initial commit exists, create the public GitHub repository:

   ```bash
   gh repo create clipforge --public --source=. --remote=origin --push \
     --description "Local-first video and audio clipping studio with AI highlights, captions, and MP4 export."
   ```

5. Confirm the files on GitHub and let the first CI run finish. Then configure
   the repository's About text/topics, enable private vulnerability reporting,
   and set default-branch rules requiring pull requests and passing CI where
   available.

## Release notes

Keep release notes focused on user-visible changes, installation requirements,
and known limitations. Do not claim verified installers or live-model behavior
unless those were exercised on the intended platform/provider. Source releases
and packaged desktop releases are different artifacts.

If binaries, tools, fonts, or models are bundled in a future release, include
their required notices alongside ClipForge's license. See
[Credits and dependencies](credits.md).
