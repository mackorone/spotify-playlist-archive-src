## Setup

This project uses [`pip-tools`](https://github.com/jazzband/pip-tools) to manage
dependencies.

To get started, first create and activate a new virtual environment:
```
$ python3.10 -m venv venv
$ source venv/bin/activate
```

Alternatively, you can install [`direnv`](https://direnv.net/) and the virtual
environment will be created for you automatically upon entering the project
directory.

Then upgrade `pip` and install `pip-tools`:
```
$ pip install --upgrade pip
$ pip install pip-tools
```

Lastly, use `pip-sync` to install the dev requirements:
```
$ pip-sync requirements/requirements-dev.txt
```

To add a new dependency, simply add a line to `requirements.in` (or
`requirements-dev.in` if it's development-only), recompile, and re-sync:
```
$ pip-compile requirements/requirements.in
$ pip-sync requirements/requirements-dev.txt
```

## Formatting

This project uses [`ruff`](https://docs.astral.sh/ruff/) to automatically
format the source code:
```
$ ruff format src/
```

## Linting

This project also uses [`ruff`](https://docs.astral.sh/ruff/) for linting, a
basic form of static analysis:
```
$ ruff check src/ [--fix]
```

## Type Checking

This project uses [`pyre`](https://github.com/facebook/pyre-check) to check for
type errors. You can invoke it from anywhere in the repository as follows:
```
$ pyre
```

Note that Pyre depends on [`watchman`](https://github.com/facebook/watchman), a
file watching service, for incremental type checking. It takes a few minutes to
install, but it's worth the investment - it makes type checking almost
instantaneous.

## Unit Testing

After making changes, you should update unit tests and run them as follows:
```
$ cd src
$ python -m unittest tests/*.py
```

## Integration Testing

To test the script locally, you can create a local `playlists` directory,
register an example playlist, export your Spotify credentials, and then run
`src/main.py`:
```
$ mkdir -p playlists/registry
$ touch playlists/registry/37i9dQZF1DX4WYpdgoIcn6
$ export SPOTIFY_CLIENT_ID='<TODO>'
$ export SPOTIFY_CLIENT_SECRET='<TODO>'
$ python src/main --playlists-dir playlists/
```

For more information about Spotify client credentials, see
https://developer.spotify.com/documentation/web-api/concepts/apps
