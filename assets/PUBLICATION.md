# Public and personal visual assets

Reviewed on 2026-10-18. The machine-readable inventory is
[publication-manifest.json](publication-manifest.json). Its hashes identify the
reviewed bytes; replacing an image requires another visual and metadata review.
The inventory is not an assertion about every file in Git history or a license
audit.

## Public defaults

- `mentor-avatars/2026-09-11/` contains generic AI character illustrations, not
  the owner's likeness. The original prompts and provenance remain alongside
  them. Four unchanged JPEG copies in the iOS `PublicMentor_*.imageset` assets
  provide a complete public default.
- `branding/` and the iOS app icon are abstract artwork without a personal face
  or journal content.
- The Feishu bot avatar depicts a fictional character. Its existing EXIF fields
  describe image dimensions and color space; the reviewed metadata did not
  contain location, device-owner or credential values.
- The reviewed wireframe screenshots contain synthetic product examples, not
  live diary records. Their source README identifies that boundary.

The PNG sources retain generation provenance metadata. The review inspected
metadata chunks separately from compressed pixel bytes, including nested EXIF
fields, rather than treating arbitrary compressed bytes as text. This is a
bounded review, not a guarantee that automated secret scanning understands image
content. Screenshot text, faces, and any future image metadata still need review.

## Personal overlay

`ios/Riji/Assets.xcassets/MentorPortraitAtlas.imageset/` is an optional local-only
overlay. The entire image set, including `Contents.json`, is ignored so a public
checkout cannot contain a dangling personal image reference. The iOS portrait
view prefers this local atlas when present, then falls back to the corresponding
public illustration and finally a system symbol.

The existing personal image bytes and the approved personal appearance were not
changed. Original HTML files containing that artwork were preserved in the
ignored local audit backup before their publication copies were sanitized.
The source design document continues to describe the owner's approved design.

Git ignore rules only protect normal source publication. Xcode compiles ignored
assets that exist in its asset catalog too. **A build made in the personal working
directory may contain the personal overlay and must not be used as a public app
release.** Build public app artifacts from a clean, reviewed source export with
the personal overlay absent. Inspect both the export and the final resource
catalog before distributing a binary. Never force-add the overlay or copy the
local audit backup into a release archive.

## Public PRD copies

The current mentor interaction `visual-prd.html` and archived copies v0.3, v0.3.1
and v0.3.2 now reference the generic illustrations by relative paths. They no
longer embed the personal atlas as base64. Each copy displays a notice explaining
that publication de-identification does not replace the approved personal visual
design or change the local app.

These HTML copies remain offline-capable when distributed with the repository's
`assets/mentor-avatars/2026-09-11/` JPEG files and the same relative directory
structure. A single HTML file by itself no longer includes those portraits.
Earlier v0.1 and v0.2 wireframes use generic inline SVG drawings and did not
contain the personal atlas.
