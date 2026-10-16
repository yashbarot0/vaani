# Vaani: user guide

Vaani turns your speech into text and pastes it where your cursor is. Everything runs on this laptop, and nothing is sent to the internet.

## After you turn on the laptop

You don't need to do anything. Vaani starts by itself when you log in.

1. Log in as usual.
2. Within a few seconds the microphone icon appears in the top panel, and a small pill appears at the bottom of the screen saying "Loading…".
3. After about 10 to 20 seconds the pill shows a microphone and "Flow". Vaani is ready.

If you press the shortcut while the pill still says "Loading…", you'll get a notification asking you to wait.

## Dictating

1. Click where you want the text, for example an email, a chat box or a document.
2. Press **Super+Alt+D**. Super is the Windows key. You'll hear a short sound, and the pill turns red with moving level bars and a timer.
3. Speak normally. The pill shows a live preview of your last few words.
4. Press **Super+Alt+D** again to stop. The pill says "Transcribing…" and then "Polishing…".
5. The text appears at your cursor, and the pill briefly shows a green "Pasted".

You can also click the pill instead of pressing the shortcut. Clicking doesn't move your cursor out of the app you're typing in.

To throw away a recording, **right-click the pill** while it's red or busy, or choose **Cancel** in the tray menu.

## Editing text with your voice (command mode)

1. Select some text.
2. Press **Super+Alt+E**.
3. Say what to do, for example "make this more formal", "fix the grammar", "summarize this in two lines", "turn this into bullet points" or "translate this to Hindi".
4. Press **Super+Alt+E** again. The result replaces your selection.

If nothing is selected, the instruction writes new text, for example "write a polite reply declining the meeting".

## Asking Bob a question

Bob's face sits in the bottom-right corner of the screen. Clicking it, or pressing **Super+Alt+B**, sends a spoken question to IBM Bob. Bob reads the answer aloud and shows it in a card. It's useful for quick questions like "what does git rebase do" or "how do I list open ports on Linux".

1. Click Bob, or press **Super+Alt+B**. A blue ring around Bob pulses with your voice, and a card above him shows your words as you speak.
2. Ask your question.
3. Click Bob, or press **Super+Alt+B**, again. A spinner circles Bob while he thinks, usually about 4 seconds.
4. Bob reads the answer aloud and his mouth moves as he speaks. The card shows the answer.

In the card and on Bob:

- **Click the card** to copy the answer.
- **Right-click Bob or the card** to cancel a question, stop Bob talking, or close the card.
- The card closes by itself a few seconds after Bob finishes speaking.

Good to know:

- **Nothing is pasted.** Your cursor and document are untouched.
- **Follow-ups work all day.** Every question that day continues the same conversation, so "and which one is faster?" makes sense. Bob's own task list gets one "Vaani voice session" entry per day. To start fresh, choose **New Bob conversation** in the tray menu.
- **This is the only feature that goes online.** The text of your question is sent to IBM. Your voice recording never leaves the laptop. Dictation and command mode stay fully offline.
- **Bob can only answer.** It runs in read-only mode and cannot change files or run commands.
- **It costs a little money.** Each question costs about 2 to 3 US cents. There is no spending limit.
- **Quiet mode:** turn off **Speak Bob's answers** in the tray menu to only show the card. **Show Bob button** hides Bob from the corner. Super+Alt+B still works.
- Starting a dictation or a new question stops Bob talking, so his voice never ends up in your recording.

## Flow and Raw modes

- **Flow**, the default, cleans your speech with a local AI. It removes "um" and "uh", fixes punctuation, applies corrections like "at ten, no wait, eleven", and turns spoken lists into real lists.
- **Raw** pastes what you said with only "um" and "uh" removed. Use it for terminals, code, or when you want your exact words.

To switch, right-click the pill while it's idle, or use **Flow mode** in the tray menu. The pill shows the current mode.

## Tips for better results

- **Speak in full sentences** at a normal pace. You don't need to say "comma" or "full stop".
- **Correct yourself out loud.** In Flow mode, "send it Tuesday, no wait, Wednesday" becomes "send it Wednesday".
- **Enumerate lists** with "first… second… third…" to get a numbered list.
- **Pause briefly before stopping** so the last word isn't cut off.
- **Teach it your words.** See "Changing settings" below.

## Tray menu

Click the microphone icon in the top panel:

- **Start or Stop dictation**
- **Ask Bob**
- **Show Bob button** and **Speak Bob's answers**
- **Cancel**
- **Flow mode (AI cleanup)**, on or off
- **Show pill when idle.** Turn this off to hide the pill until you start dictating.
- **New Bob conversation**, which makes Bob forget the previous questions
- **Copy raw**, which copies the last thing you said exactly as Whisper heard it, before cleanup
- **Quit**

After **Quit**, Vaani stays off until you log in again or start it yourself:

```sh
systemctl --user start vaani
```

## Changing settings

Create or edit `~/.config/vaani/config.toml`, then restart Vaani:

```sh
systemctl --user restart vaani
```

The most useful settings:

```toml
# Names and terms you use often, so they're spelled right
vocabulary = ["CUDA", "PyTorch", "Fedora", "Ghostty"]

# Words it keeps mishearing: "what it hears" = "what you meant"
replacements = { "pie torch" = "PyTorch" }

mode = "flow"           # or "raw"
live_preview = true     # live words in the pill while recording
sounds = true           # start and stop sounds
history = false         # true saves every dictation to ~/.local/share/vaani/history.jsonl
max_seconds = 300       # recording stops by itself after 5 minutes
```

The full list of settings is at the top of `~/projects/vaani/vaani/config.py`. A typo in a setting name stops Vaani from starting. The logs then name the bad setting.

## Useful commands

```sh
~/projects/vaani/bin/vaani status    # is it running, which mode, is the AI ready
systemctl --user restart vaani         # restart it
systemctl --user stop vaani            # stop it until next login
journalctl --user -u vaani -n 50       # recent logs, if something goes wrong
```

## Troubleshooting

**The shortcut does nothing and there's no pill or tray icon.**
Vaani isn't running. Run `systemctl --user restart vaani`. If it still doesn't appear, check the logs with the journalctl command above.

**The pill says "No speech heard".**
The microphone heard nothing loud enough. Check that the mic isn't muted and that the right input is chosen in Settings, Sound. Speak a little louder or closer. In a very quiet room, you can lower `silence_rms` from 0.04 to 0.03.

**The pill says "Copied: Ctrl+V" instead of "Pasted".**
Vaani lost permission to press the paste key. Run `getfacl -p /dev/uinput | grep $USER`. If nothing is printed, run `./setup.sh uinput` from the project folder, then restart Vaani.

**Text doesn't paste in one particular app.**
Vaani pastes with Shift+Insert. Some apps, such as Zed, may not have that key bound. Either bind Shift+Insert to paste in that app, or add `paste_keys = ["KEY_LEFTCTRL", "KEY_V"]` to your settings. Ctrl+V won't work in terminals, though.

**Flow mode stopped cleaning text.**
Run `bin/vaani status`. If it says `llm=off`, the AI model failed to start, and the logs say why. Dictation still works and pastes the uncleaned text.

**After a big system update, the pill says "Speech engine failed to start".**
The AI engines were built against the CUDA 13.2 toolkit in `/usr/local/cuda-13.2`. If that toolkit was removed or upgraded, rebuild them:

```sh
cd ~/projects/vaani && ./setup.sh build && systemctl --user restart vaani
```

NVIDIA driver updates on their own don't need a rebuild.

**The card says "Bob helper not running".**
The online helper is stopped. Run `systemctl --user restart vaani-bob`, and check `journalctl --user -u vaani-bob -n 20` if it stops again.

**The card says "Bob key expired or invalid".**
Bob API keys expire. Put the new key in `~/.config/vaani/bob.env` as `BOB_API_KEY=...`, then run `systemctl --user restart vaani-bob`.

**The card says "Can't reach IBM Bob".**
The laptop is offline or IBM's service is down. Dictation still works.

## Things to avoid

- **Don't move or rename the project folder.** The startup service and shortcuts point to `~/projects/vaani`. If you do move it, run `./setup.sh desktop` from the new location.
- **Don't delete the `models` folder.** It holds the AI models. Getting them back means downloading about 3 GB again with `./setup.sh models`.

## Turning it off permanently

```sh
systemctl --user disable --now vaani
```

To turn it back on:

```sh
systemctl --user enable --now vaani
```
