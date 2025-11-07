AWFL CLI demo guide

This guide shows how to record a short terminal demo (asciinema), publish it, and export a GIF/SVG you can embed in README.

What you’ll produce
- A .cast file recorded with asciinema (source of truth)
- Optional: a public asciinema page and embed URL
- Optional: a GIF (good for GitHub README previews)
- Optional: an SVG (infinite-resolution playback; works with asciicast v1/v2 only)

Prerequisites
- asciinema for recording
  - Recommended: record with asciinema v2 because popular exporters (agg, svg-term) don’t support asciicast v3 yet
  - Quick, no-install option: pipx run --spec 'asciinema==2.4.0' asciinema rec <file.cast>
  - macOS: brew install asciinema (may install v2)
  - Linux: pipx install 'asciinema==2.4.0' or use your package manager (ensure it’s v2)
- Optional: GIF export (agg)
  - Go (recommended):
    - Install Go toolchain if needed (macOS): brew install go, or download from https://go.dev/dl/
    - Install agg: go install github.com/asciinema/agg@latest
    - Ensure $GOBIN or GOPATH/bin (default: $HOME/go/bin) is on PATH:
      - export PATH="$HOME/go/bin:$PATH"
      - Persist by adding the line above to ~/.zshrc or ~/.bashrc
  - Docker (no local install):
    - docker run --rm -v "$PWD":/data asciinema/asciicast2gif INPUT.cast OUTPUT.gif
      Replace INPUT.cast/OUTPUT.gif with your filenames.
  - Prebuilt binaries:
    - Download the latest release for your OS: https://github.com/asciinema/agg/releases
    - Put the 'agg' binary on your PATH (e.g., /usr/local/bin)
- Optional: SVG export
  - Requires either a globally installed svg-term or Node.js + npm on PATH so the script can run npx --package=svg-term-cli
  - Note: SVG export works for asciicast v1/v2 only (not v3)

Quick start
1) Record
- Use the helper script to record a 60–90s session:
  - ./scripts/record_demo.sh record recordings/awfl-quickstart.cast
  - Or force v2 recording without installing anything:
  - pipx run --spec 'asciinema==2.4.0' asciinema rec recordings/awfl-quickstart.cast
  - Tip: Resize your terminal to ~100x28, clear the screen, and use a clean repo to avoid noisy diffs.
  - Stop recording with Ctrl-D (or type exit).

2) Export a GIF (optional)
- Requires agg:
  - ./scripts/record_demo.sh gif recordings/awfl-quickstart.cast docs/awfl-quickstart.gif
  - If agg is missing, the script prints install guidance (Go, Docker, Releases).

3) Export an SVG (optional)
- Requires either a global svg-term or npx (Node.js/npm) to fetch svg-term-cli on the fly. No local package.json is used or required:
  - ./scripts/record_demo.sh svg recordings/awfl-quickstart.cast docs/awfl-quickstart.svg
  - Note: Only asciicast v1/v2 are supported by svg-term.

4) Publish the cast (optional)
- Upload to asciinema and copy the share URL for README linking:
  - ./scripts/record_demo.sh upload recordings/awfl-quickstart.cast

README integration
- Link to this guide and, if you exported one, preview with a GIF:
  - See docs/DEMO.md for recording details.
  - Optionally add a preview image:
    - ![AWFL quickstart demo](docs/awfl-quickstart.gif)
  - Or link to the asciinema page for interactive playback:
    - https://asciinema.org/a/YOUR_CAST_ID

Tips for great recordings
- Terminal size: Use a consistent width/height (e.g., 100x28). Avoid line wraps.
- Prompt: Keep it minimal to reduce noise. You can temporarily set PS1 to a short prompt.
- Font/contrast: Choose a readable theme with good contrast.
- Practice once: Do a dry run so the final recording is smooth and under 90 seconds.
- Keep it real: Use real commands; avoid editing files in Vim/Nano mid-recording unless necessary.

Troubleshooting
- Asciicast v3 not supported by exporters:
  - If svg/gif export fails with “only asciicast v1 and v2 formats can be opened”, your .cast is v3.
  - Re-record with asciinema v2:
    - pipx run --spec 'asciinema==2.4.0' asciinema rec recordings/awfl-quickstart.cast
    - or pipx install 'asciinema==2.4.0' && asciinema rec recordings/awfl-quickstart.cast
- agg not installed:
  - Install via Go: go install github.com/asciinema/agg@latest
  - Or avoid local install with Docker: docker run --rm -v "$PWD":/data asciinema/asciicast2gif INPUT.cast OUTPUT.gif
  - Or use a prebuilt binary from Releases and place it on your PATH
  - Ensure $HOME/go/bin is on PATH if using Go (export PATH="$HOME/go/bin:$PATH")
- Command not found: Ensure asciinema is installed. For SVG export, make sure either svg-term is installed globally or Node.js + npm are on PATH so the script can run npx --package=svg-term-cli.
- GIF too large: Re-record shorter, or consider SVG/asciinema link instead of GIF.
- Terminal size ignored: Resize your terminal window before recording; asciinema captures the actual size.
