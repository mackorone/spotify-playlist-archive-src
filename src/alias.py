#!/usr/bin/env python3

import string


class InvalidAliasError(Exception):
    pass


class Alias(str):
    def __new__(cls, alias: str) -> str:
        if (
            not alias
            or cls._contains_invalid_whitespace(alias)
            or cls._contains_enclosing_whitespace(alias)
        ):
            raise InvalidAliasError(alias)
        return super().__new__(cls, alias)

    @classmethod
    def _contains_invalid_whitespace(cls, candidate: str) -> bool:
        invalid_chars = set(string.whitespace) - set(" \t")
        return bool(invalid_chars & set(candidate))

    @classmethod
    def _contains_enclosing_whitespace(cls, candidate: str) -> bool:
        return candidate.strip() != candidate
