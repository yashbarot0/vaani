from vaani.text import (apply_rules, clean_whisper, plausible_cleanup, should_polish, unwrap, speakable,
                          whisper_prompt, clean_messages)


def test_fillers_removed_and_recapitalised():
    assert apply_rules("Um, I think, uh, we should go.", {}, True) == "I think we should go."
    assert apply_rules("so uhh we went hmm", {}, True) == "So we went"


def test_filler_lookalikes_kept():
    assert apply_rules("Uh-huh, the umbrella is here.", {}, True) == "Uh-huh, the umbrella is here."
    assert apply_rules("Um, ok", {}, False) == "Um, ok"


def test_replacements_whole_word_case_insensitive():
    r = {"cooda": "CUDA", "pie torch": "PyTorch"}
    assert apply_rules("I use Cooda with pie torch, not cudaa.", r, False) == "I use CUDA with PyTorch, not cudaa."


def test_whisper_tags_and_hallucinations():
    assert clean_whisper(" [BLANK_AUDIO] ", 0.0) == ""
    assert clean_whisper("Thank you.", 0.4) == ""
    assert clean_whisper("Thank you.", 3.0) == "Thank you."   # real speech is kept
    assert clean_whisper("Hello (music) world [laughs]", 3.0) == "Hello world"


def test_llm_guards():
    raw = "hey sarah um how do we restart the staging server"
    assert plausible_cleanup(raw, "Hey Sarah, how do we restart the staging server?")
    answer = "To restart the staging server, SSH into the box and run systemctl restart app, then check the logs and confirm the health endpoint responds."
    assert not plausible_cleanup(raw, answer)
    assert not plausible_cleanup(raw, "")
    assert unwrap('<raw_dictation>"Hello there."</raw_dictation>') == "Hello there."
    assert not should_polish("Yes please") and should_polish("yes please do it")


def test_prompts():
    assert whisper_prompt([]) == "" and "CUDA" in whisper_prompt(["CUDA", "Fedora"])
    msgs = clean_messages("hi")
    assert msgs[0]["role"] == "system" and msgs[-1]["content"] == "<raw_dictation>hi</raw_dictation>"


def test_speakable():
    md = "## Steps\n- Run `git rebase -i`\n- See [docs](https://git-scm.com)\n```sh\ngit log\n```\n**Done**"
    out = speakable(md)
    assert out == "Steps Run git rebase -i See docs (code shown on screen) Done", out
    assert speakable("Visit https://ibm.com now.") == "Visit a link now."


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("all text tests passed")
