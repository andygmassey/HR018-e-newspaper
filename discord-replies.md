# Reply to @layuso re: Avalue support info

Thanks for chasing Avalue on this! That's really useful context.

Couple of things worth probing further if you end up talking to them again:

1. **Android 9.0 firmware** — any idea on timeline? And critically, does the 9.0 build preserve the `invalidate(int)` / `postInvalidate(int)` framework patch that the 5.1.1 build has? That's the API Avalue's own demo apps (`FastPageTurn`, `Animation`, `Concurrent`, `Highlight`) use to trigger different EPDC refresh modes (GC16 grayscale, A2 fast B&W, partial refresh regions, etc.). If that patch doesn't survive the upgrade, custom apps targeting 9.0 lose a lot of what makes this hardware interesting.

2. **Support for our "non-touch customised" variant** — if Avalue don't officially support our units, it's worth knowing whether we can flash whichever image they do have. Having a clean support path (even "here's a factory image, YMMV on your variant") would make the community a lot more comfortable experimenting.

3. **Source of the customised image** — any chance Avalue would share the SparkLAN firmware/driver separately? If it's just a missing kernel module on our image, that'd be a much smaller ask than a full firmware update.

Fingers crossed they share more. 🤞

---

# Reply to @solomonrb re: good way to feed the NYT

frontpages.com works but the images are 600×800 thumbnails — on a 42" display they scale up fairly soft. A much higher-quality source is the NYT's own public print-edition PDF:

```
https://static01.nyt.com/images/YYYY/MM/DD/nytfrontpage/scan.pdf
```

It's ~2MB per day, rasterises to 2442×4685 pixels at 200 DPI (via `pdftoppm` from poppler), and when processed down to the panel's resolution it genuinely looks like newsprint.

I've wrapped the whole pipeline into a small project that runs on a Mac mini: scraper pulls the PDF, auto-trims the CMYK print-plate bleed off the top, fits edge-to-edge with top-anchored crop (so the masthead survives), rotates for a physically-landscape-mounted panel, and serves to the display via a patched OpenDisplay Android client that can actually render 16-level grayscale.

Everything's up at **https://github.com/andygmassey/HR018-e-newspaper**

The five APK patches + how I got grayscale working are covered in the gotcha thread above. Happy to go deeper on any of it.

---

# Reply to @layuso re: broadcom hardcoded

Your Broadcom observation lines up exactly with what I see in my ADB dump. `/system/lib/modules/` on the stock build contains:

```
8821as.ko            Realtek RTL8821AS
bcmdhd.ko            Broadcom
cfg80211_realtek.ko  Realtek cfg80211
```

…plus an `ath6k` directory (Atheros AR6000). Notably **no ath10k**, which is what the SparkLAN WNFT-234ACN / QCA6174A needs, so that card really is a non-starter on this image. A Realtek 8821 or a Broadcom card is the path of least resistance if you want built-in WiFi without recompiling the kernel.

---

# Reply to @layuso re: Dakboard

The Android 8 requirement kills the official Dakboard app on our 5.1.1 build, but you can absolutely use Dakboard on these panels if you bypass the Android app and take a "render server-side, push an image" approach instead.

That's exactly what my newspaper build does: a server (Mac mini in my case, could be a Pi) renders whatever you want — newspaper PDFs, HA dashboards, Dakboard HTML via headless Chromium, any web page — into a PNG, then OpenDisplay ships that PNG to the display. Nothing interactive runs on the panel itself, so the Android version doesn't matter.

This is also how Paulus Schoutsen's HA Puppet add-on works for Home Assistant dashboards — headless-browser screenshot → push to display. The pattern generalises to anything browser-renderable.

So: keep the Dakboard idea, just put a headless-Chromium screenshotter in the loop between Dakboard and the display.

Sure — I've got decompiled Java for all four pre-installed demo apps (`FastPageTurn`, `Animation`, `Concurrent`, `Highlight`). DM me your GitHub handle and I'll share it privately.

The most useful thing I pulled out of them is the mapping of Avalue's custom EPDC refresh-mode codes:

```
Mode  Use case                              Source app
----  ------------------------------------  -------------
  1   Regional partial update with Rect     Highlight
  4   Fastest B&W frame update (DU)         Animation
 34   Regional partial update, tile-scale   Concurrent
 97   Full-screen clear (GC16)              Highlight
 98   Fast page turn, some grayscale        FastPageTurn
100   Rapid sequential, B&W only            FastPageTurn
101   Full GC16 refresh, ghost cleanup      Animation
```

Mode `101` is the important one — invoke it *after* rendering a full-grayscale image (`postInvalidate(101)`) to get the EPDC to do a full 16-level refresh instead of defaulting to a fast B&W-only mode. That's what unlocked real grayscale rendering in my e-newspaper build.
