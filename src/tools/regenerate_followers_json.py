#!/usr/bin/env python3

import argparse
import datetime
import json
import pathlib
import subprocess


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--archive-dir",
        type=pathlib.Path,
        help="Path to a local copy of spotify-playlist-archive",
        required=True,
    )
    parser.add_argument(
        "--output-dir",
        type=pathlib.Path,
        help="The directory where the followers JSON files should be written",
        required=True,
    )
    parser.add_argument(
        "--initial-commit",
        type=pathlib.Path,
        help="Get followers going back as far as this commit (but not including)",
        # This is the commit that added num_followers logic
        default="9cb281b85507108508d5736f57a39866881e1886",
    )
    args = parser.parse_args()

    # Make sure the output dir exists
    args.output_dir.mkdir(exist_ok=True, parents=True)

    # Iterate through commits
    commits = subprocess.run(
        [
            "git",
            "-C",
            args.archive_dir,
            "rev-list",
            "--reverse",
            f"{args.initial_commit}..HEAD",
        ],
        stdout=subprocess.PIPE,
        text=True,
    ).stdout.splitlines()

    for commit in commits:
        # Check out the commit
        print(f"Checking out commit: {commit}")
        subprocess.run(["git", "-C", args.archive_dir, "checkout", commit])

        # Get the date of the commit
        output = subprocess.run(
            [
                "git",
                "-C",
                args.archive_dir,
                "show",
                "-s",
                "--format=%cd",
                "--date=format:%Y-%m-%d",
            ],
            stdout=subprocess.PIPE,
            text=True,
        ).stdout.strip()
        date = datetime.datetime.strptime(output, "%Y-%m-%d").date()

        # Get the num_followers for each commit
        i = 0
        for input_path in (args.archive_dir / "playlists" / "pretty").iterdir():
            if input_path.suffix != ".json":
                continue
            i += 1
            if i % 100 == 0:
                print(f"{date} - {i} - {input_path.name}")
            with open(input_path) as f:
                data = json.load(f)
            num_followers = data.get("num_followers")
            if num_followers is None:
                continue
            output_path = args.output_dir / input_path.name
            try:
                with open(output_path) as f:
                    prev_content = json.load(f)
            except FileNotFoundError:
                prev_content = {}
            prev_content[str(date)] = num_followers
            with open(output_path, "w") as f:
                json.dump(prev_content, f, indent=2, sort_keys=True)


if __name__ == "__main__":
    main()
