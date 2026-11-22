An assistant is doing this task on the user's Windows laptop: {goal}
Windows open now, front first: {windows}

Pick 1 to 3 checks that, if they ALL pass, prove the whole task is done. Use only these, one per line:
  window_open "text"      a window whose title or program contains the text
  front_window "text"     the window in front has the text in its title
  page_has "text"         the window in front shows the text
  page_lacks "text"       the window in front does not show the text
  fullscreen              the window in front fills the screen
  dark_mode on|off        Windows' colour mode
  process_running "name"  a program with that name is running
If no check from this list can prove it (for example a video really playing, or what an email says), answer NONE.
Answer with the check lines only.
