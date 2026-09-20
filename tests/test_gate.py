"""The voice gate: Rocky never obeys its own voice, the user can talk over it, and 'stop' always lands."""

from rocky.voice import STOP_WORDS, Gate


def _gate():
    g = Gate()
    g.on_event("said:Playing twinkle twinkle.")
    return g


def test_pure_echo_is_dropped():
    assert _gate().scrub("Play twinkle twinkle") == ("", True)


def test_user_words_survive_next_to_the_echo():
    assert _gate().scrub("playing twinkle twinkle watermelon open safari") == ("watermelon open safari", True)


def test_user_only_transcript_untouched():
    assert _gate().scrub("watermelon open notes") == ("watermelon open notes", False)


def test_stop_survives_the_scrub_and_matches():
    kept, _ = _gate().scrub("Playing twinkle twinkle. Stop")
    assert kept == "stop" and STOP_WORDS.match(kept)
    assert STOP_WORDS.match("Stop stop stop.") and not STOP_WORDS.match("stop the video")


def test_follow_up_window():
    g = Gate()
    assert not g.follow_up_open()
    g.open_follow_up()
    assert g.follow_up_open()


def test_blurred_echo_of_the_reply_is_dropped():
    g = Gate()
    g.on_event("said:Opening Notes.")
    assert g.scrub("Open Notes") == ("", True)
    assert g.scrub("watermelon open safari") == ("watermelon open safari", False)


def test_same_command_again_without_wake_word_is_a_repeat():
    g = Gate()
    g.note_command("open notes")
    assert g.is_repeat("Open Notes")
    assert not g.is_repeat("open safari")
