## Quick Start (Linux/Bash)
```bash
# 0. If you haven't done so already, install direnv
curl -sfL https://direnv.net/install.sh | bash
echo 'eval "$(direnv hook bash)"' >> ~/.bashrc

# 1. Clone the repo locally
git clone git@github.com:mackorone/spotify-playlist-archive-src.git

# 2. Enter the repo to automatically create and active a virtualenv
cd spotify-playlist-archive-src

# 3. Install pip-tools in the virtualenv
pip install pip-tools

# 4. Install project dependencies in the virtualenv using pip-tools
pip-sync requirements/requirements-dev.txt

# 5. Install pre-commit hooks
pre-commit install
```

## [pip-tools](https://github.com/jazzband/pip-tools)

This project uses `pip-tools` to manage dependencies. To get started, first
create and activate a new virtual environment:
```bash
python3.10 -m venv venv
source venv/bin/activate
```

(Alternatively, you can install [`direnv`](https://direnv.net/) and the virtual
environment will be created for you automatically upon entering the project
directory.)

Then optionally upgrade `pip` and install `pip-tools`:
```bash
pip install --upgrade pip
pip install pip-tools
```

Lastly, use `pip-sync` to install the dev requirements:
```bash
pip-sync requirements/requirements-dev.txt
```

To add a new dependency, simply add a line to `requirements.in` (or
`requirements-dev.in` if it's development-only), recompile, and re-sync:
```bash
pip-compile requirements/requirements.in
pip-sync requirements/requirements-dev.txt
```

## [pre-commit](https://pre-commit.com/)

This project uses `pre-commit` for installing pre-commit hooks, which prevent
lint or type errors from being committed.
```bash
# Run this after `pip-sync requirements/requirements-dev.txt`
pre-commit install
```
To add new hooks, simply modify `.pre-commit-config.yaml`.

## [Ruff](https://docs.astral.sh/ruff/)

This project uses Ruff for linting and code formatting:
```bash
# Print lint errors
ruff check src/ [--show-source]

# Attempt to fix errors
ruff check src/ --fix

# Format the source code
ruff format src/
```

You may find it helpful to create Bash aliases for these commands, like so:
```bash
# ~/.bashrc

repo_root() {
    git rev-parse --show-toplevel
}
alias check='ruff check $(repo_root)/src/ --show-source'
alias format='ruff format $(repo_root)/src/'
```

## [Pyre](https://pyre-check.org/)

This project uses the Pyre type checker. You can invoke it from anywhere in the
repository as follows:
```bash
pyre
```

Note that Pyre depends on [`watchman`](https://github.com/facebook/watchman), a
file watching service, for incremental type checking. It takes a few minutes to
install, but it's worth the investment - it makes type checking almost
instantaneous.

## Unit Testing

After making changes, you should update unit tests and run them as follows:
```bash
cd src
python -m unittest tests/*.py
```

You may find it helpful to create a Bash alias for quickly running tests:
```bash
# ~/.bashrc

repo_root() {
    git rev-parse --show-toplevel
}
alias run-tests='(cd $(repo_root)/src/ && python -m unittest tests/*.py)'
```

## Integration Testing

To test the script locally, you can create a local `playlists` directory,
register an example playlist, export your Spotify credentials, and then run
`src/main.py`:
```bash
mkdir -p playlists/registry
touch playlists/registry/37i9dQZF1DX4WYpdgoIcn6
export SPOTIFY_CLIENT_ID='<TODO>'
export SPOTIFY_CLIENT_SECRET='<TODO>'
python src/main --playlists-dir playlists/
```

For more information about Spotify client credentials, see
https://developer.spotify.com/documentation/web-api/concepts/apps
