"""contest.names: display-name rules. Pure functions."""
import pytest

from contest.names import NameInvalid, name_key, suggest_display_name, validate_display_name


def bad(raw):
    with pytest.raises(NameInvalid) as e:
        validate_display_name(raw)
    return e.value.code


def test_cleans_and_keys_a_good_name():
    assert validate_display_name("  Alex   Morgan ") == ("Alex Morgan", "alexmorgan")
    assert validate_display_name("O'Brien")[0] == "O'Brien" and validate_display_name("Zoe-Lee")[1] == "zoelee"
    assert validate_display_name("Max_1")[0] == "Max_1"
    assert validate_display_name("Jose")[1] == validate_display_name("JOSE")[1] == validate_display_name("j.o.s.e")[1]      # one name to the uniqueness check


def test_length_and_characters():
    assert bad("A") == "NAME_INVALID" and bad("x" * 21) == "NAME_INVALID" and bad("") == "NAME_INVALID" and bad(None) == "NAME_INVALID"
    validate_display_name("Al")
    validate_display_name("x" * 20)
    assert bad("bad@name") == "NAME_INVALID" and bad("a<b>") == "NAME_INVALID" and bad("hi​there") == "NAME_INVALID"      # zero-width space
    assert bad(".Alex") == "NAME_INVALID" and bad("Alex-") == "NAME_INVALID" and bad("..") == "NAME_INVALID"
    assert bad("-_") == "NAME_INVALID"


def test_links_and_reserved_names():
    assert bad("Tom.com") == "NAME_INVALID" and bad("www tom") == "NAME_INVALID" and bad("http tom") == "NAME_INVALID"
    for r in ("Admin", "HOUSE", "you", "B e e z y", "Mod.erator"):
        assert bad(r) == "NAME_RESERVED"


def test_other_alphabets_are_fine():
    assert validate_display_name("José")[0] == "José"
    assert validate_display_name("李娜")[1] == "李娜"


def test_name_key_drops_everything_but_letters_and_digits():
    assert name_key("A.l-e x_1") == "alex1"


def test_suggestions_come_from_the_account_and_always_validate():
    assert suggest_display_name(username="alexm") == "alexm"
    assert suggest_display_name(first="Alex", last="Morgan") == "Alex M"
    assert suggest_display_name(email="jordan.lee_77@gmail.com") == "Jordan Lee 77"
    assert suggest_display_name(username="a", first="Sam") == "Sam"                    # an unusable username falls through to the next idea
    assert suggest_display_name() == "Player" and suggest_display_name(email="@x.com") == "Player"
    assert suggest_display_name(username="admin", first="Pat", last="Quinn") == "Pat Q"  # a reserved suggestion is skipped
