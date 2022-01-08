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
    - https://next.github.com/projects/flat-data

- Codebase
    - Merge cumulative regeneration code
    - Measure code coverage and add missing tests
