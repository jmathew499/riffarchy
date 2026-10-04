# System install, used by the AUR PKGBUILD:  make DESTDIR="$pkgdir" PREFIX=/usr install
# For a per-user install that runs straight from this checkout, use ./install.sh instead.
PREFIX ?= /usr
DESTDIR ?=
APPID := app.riffarchy.Riffarchy
LIBDIR := $(PREFIX)/lib/riffarchy

.PHONY: install uninstall

install:
	install -Dm755 riffarchy.py "$(DESTDIR)$(LIBDIR)/riffarchy.py"
	install -Dm644 -t "$(DESTDIR)$(LIBDIR)/riffcore" riffcore/*.py
	python -m compileall -q -d "$(LIBDIR)" "$(DESTDIR)$(LIBDIR)"
	install -d "$(DESTDIR)$(PREFIX)/bin"
	ln -sf ../lib/riffarchy/riffarchy.py "$(DESTDIR)$(PREFIX)/bin/riffarchy"
	install -Dm644 data/$(APPID).desktop "$(DESTDIR)$(PREFIX)/share/applications/$(APPID).desktop"
	install -Dm644 data/$(APPID).svg "$(DESTDIR)$(PREFIX)/share/icons/hicolor/scalable/apps/$(APPID).svg"
	install -Dm644 LICENSE "$(DESTDIR)$(PREFIX)/share/licenses/riffarchy/LICENSE"

uninstall:
	rm -rf "$(DESTDIR)$(LIBDIR)"
	rm -f "$(DESTDIR)$(PREFIX)/bin/riffarchy" \
	      "$(DESTDIR)$(PREFIX)/share/applications/$(APPID).desktop" \
	      "$(DESTDIR)$(PREFIX)/share/icons/hicolor/scalable/apps/$(APPID).svg" \
	      "$(DESTDIR)$(PREFIX)/share/licenses/riffarchy/LICENSE"
