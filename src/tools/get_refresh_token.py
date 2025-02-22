#!/usr/bin/env python3

import argparse
import asyncio
import os
import urllib.parse

import aiohttp


async def login() -> None:
    # Login OAuth flow.
    #
    # 1. Opens the authorize url in the default browser (on Linux).
    # 2. Sets up an HTTP server on port 8000 to listen for the callback.
    # 3. Requests a refresh token for the user and prints it.

    # Build the target URL
    client_id = os.getenv("SPOTIFY_CLIENT_ID")
    client_secret = os.getenv("SPOTIFY_CLIENT_SECRET")
    assert client_id and client_secret
    query_params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": "http://localhost:8000",
        # Both scopes are useful when fetching playlists
        "scope": "playlist-read-private playlist-read-collaborative",
    }
    target_url = "https://accounts.spotify.com/authorize?{}".format(
        urllib.parse.urlencode(query_params)
    )

    # Print and try to open the URL in the default browser.
    print("Opening the following URL in a browser (at least trying to):")
    print(target_url)
    os.system("xdg-open '{}'".format(target_url))

    # Set up a temporary HTTP server and listen for the callback
    import socketserver
    from http import HTTPStatus
    from http.server import BaseHTTPRequestHandler

    authorization_code: str = ""

    class RequestHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            nonlocal authorization_code
            request_url = urllib.parse.urlparse(self.path)
            q = urllib.parse.parse_qs(request_url.query)
            authorization_code = q["code"][0]

            self.send_response(HTTPStatus.OK)
            self.end_headers()
            self.wfile.write(b"OK!")

    PORT = 8000
    httpd = socketserver.TCPServer(("", PORT), RequestHandler)
    httpd.handle_request()
    httpd.server_close()

    # Request a refresh token for given the authorization code
    refresh_token = await get_refresh_token(
        client_id=client_id,
        client_secret=client_secret,
        authorization_code=authorization_code,
    )

    print("")
    print("--- Refresh token ---")
    print(refresh_token)


async def get_refresh_token(
    client_id: str,
    client_secret: str,
    authorization_code: str,
) -> str:
    async with aiohttp.ClientSession() as session:
        async with session.post(
            url="https://accounts.spotify.com/api/token",
            data={
                "grant_type": "authorization_code",
                "code": authorization_code,
                "redirect_uri": "http://localhost:8000",
            },
            auth=aiohttp.BasicAuth(client_id, client_secret),
        ) as response:
            data = await response.json(content_type=None)

    error = data.get("error")
    if error:
        raise Exception("Failed to get refresh token: {}".format(error))

    refresh_token = data.get("refresh_token")
    if not refresh_token:
        raise Exception("Invalid refresh token: {}".format(refresh_token))

    token_type = data.get("token_type")
    if token_type != "Bearer":
        raise Exception("Invalid token type: {}".format(token_type))

    return refresh_token


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Obtain a refresh token through the OAuth flow"
    )
    parser.parse_args()
    await login()


if __name__ == "__main__":
    asyncio.run(main())
