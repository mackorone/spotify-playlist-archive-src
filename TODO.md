- Cumulative playlist improvements
    - More data in markdown files (description, etc.)
    - More data in published playlists (date first scraped, etc.)
    - Clean up near-duplicates in cumulative playlists
        - Key: title, artists, and duration
            - Check that this doesn't have false positives
        - Album can (and often does) differ:
            - https://open.spotify.com/track/2nG54Y4a3sH9YpfxMolOyi
            - https://open.spotify.com/track/2z0IupRlVRlDN5r2IVqHyN
    - Update data for all tracks, not just current tracks
        - How to handle tracks that are removed from Spotify

- Features
    - Don't update snapshot ID if no other changes
    - Debug perf issues: https://github.com/github/git-sizer
        - Add intermediate directories to playlist dirs
        - (Maybe) delete old cumulative playlist blobs
    - https://next.github.com/projects/flat-data

- Codebase
    - More unit tests for playlist updater
        - only_fetch_these_playlists
    - Fix code complexity of playlist updater
    - Write wrapper class for path-related logic
        - Add getter methods for all paths
        - Only registry dir should be enumerable
    - Separate class for SpotifyPlaylist
        - No concept of "original" vs "unique" name
        - Consider using library for serialization
    - Refactor committer to use GitUtils, add tests
    - Merge cumulative regeneration code
    - Measure code coverage and add missing tests
        - .coveragerc file should include:
          [report]
          exclude_lines = @external
