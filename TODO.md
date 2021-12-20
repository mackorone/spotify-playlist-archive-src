- Cumulative playlist improvements
    - Add date registered info to cumulative playlists markdown
    - Link to published cumulative playlists
    - Clean up near-duplicates in cumulative playlists
        - Key: title, artists, and duration
            - Check that this doesn't have false positives
        - Album can (and often does) differ:
            - https://open.spotify.com/track/2nG54Y4a3sH9YpfxMolOyi
            - https://open.spotify.com/track/2z0IupRlVRlDN5r2IVqHyN
    - Update date for all tracks, not just current tracks
        - How to handle tracks that are removed from Spotify
    - Sort cumulative playlists (date added, album, etc.)
        - Potentially related to "Flat Data", see below
    - Describe "Added" and "Removed" dates
        - "Added" represents the first date that the track appeared in the
          playlist, to our best knowledge. We can't know if a track was added
          and then removed prior to the playlist being added to the index.
            - The "*" symbol indicates that the track belonged to the first
              version of the playlist that was indexed, but it's too late to go
              back and check when the track was originally added
        - "Removed" represents the most recent date that the track was removed
          from the playlist, and is empty/null if the track is still present

- Features
    - Automatically add all "Spotify" user playlists
    - Look into Flat Data: https://next.github.com/projects/flat-data

- Codebase
    - Add more unit tests
    - Use f-strings everywhere
    - Run the script from anywhere in repo
    - Integration test for README.md updates
    - Replace shell commands with Python lib
    - Replace from_json with Python lib

- Performance
    - Only fetch each track once (same track in multiple playlists)
