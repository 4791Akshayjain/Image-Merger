# Image Merger

Image Merger stores one or two images inside a PNG (or JPEG) that displays a separate picture when opened normally. Each hidden image is scrambled using its own numeric code and can be recovered with the matching code.

## Download the complete application

For the complete, ready-to-use application, download the latest Windows installer from this project's **Releases** page and run it. The installer is the recommended way to get the app; you do not need to install Python or download the source code.

## Merge images

1. Open **Image Merger** and choose **Merge images**.
2. Select **Image A** and enter a numeric code from 3 to 20 digits.
3. Optionally, select **Image B** and enter a different numeric code. Leave both Image B fields empty to merge only one hidden image.
4. Select the **carrier image**. This is the visible picture in the merged file.
5. Choose the **hidden image quality** and the **carrier output** options (see below). The text under each option describes exactly what will happen.
6. Choose **Create merged image...** and select where to save the file. When it finishes, the app shows how large the carrier and each hidden image are inside the result.

Keep a record of each code you use. You will need the matching code to recover each image. If you add Image B, its code must be different from Image A's; leading zeros do not make a code different (for example, `007` and `7` count as the same code).

The carrier and hidden images can be selected by clicking the boxes or dragging and dropping files onto them. The app accepts PNG, JPG/JPEG, BMP, GIF, WebP, and TIFF images as inputs.

### Hidden image quality

Hidden images are scrambled in 16x16 or 8x8 pixel blocks and then stored as WebP.

| Preset | WebP quality | Use it when |
| --- | --- | --- |
| Smallest | 50 | file size matters most; fine detail will soften |
| Balanced | 80 | default; looks like the original at a fraction of the size |
| High | 92 | you want a very close copy |
| Lossless | exact | you need the exact pixels (largest files) |

You can also drag the slider for any value from 1 to 100.

**Scramble strength** chooses the block size used to scramble each hidden image: 16x16 (default, smaller files) or 8x8 (the image is cut into four times as many pieces, so it is harder to reassemble, at the cost of slightly larger files). The block size is stored in the file, so extraction needs no setting.

### Carrier output

The visible carrier is usually the largest part of the merged file, so it has its own options:

- **Format:** PNG (lossless, larger) or JPEG (much smaller; the hidden data is appended after the picture, which viewers ignore).
- **Size:** keep the original size, or limit the longest side to 2560, 1920 or 1280 px.
- **Reduce to 256 colors** (PNG only): smaller files, with some banding in photos.
- **JPEG quality** (JPEG only): 50-100.

A JPEG carrier cannot keep transparency; transparent areas become white.

## Extract an image

1. Choose **Extract image**.
2. Select the merged PNG or JPEG.
3. Enter the code used for the image you want to recover.
4. Choose **Extract** to preview the result, then **Save As PNG...** to save it.

Merged PNGs created by earlier versions of the app can still be extracted.

## Important notes

- Keep the merged file unchanged. Editing, recompressing, or re-saving it in another app may remove the hidden image data.
- **Do not send it as a photo through WhatsApp, Instagram, Telegram (as photo), Messenger and similar apps.** They recompress pictures and strip the hidden data. Send the file as a **document/file**, as an email attachment, or through a cloud link instead.
- Lossy hidden images are not identical to the originals. Use the Lossless preset if you need exact pixels.
- Each hidden image is limited to 64 MB of raw pixel data (roughly 21 megapixels for an RGB image; images with transparency have a lower pixel limit) and 16,000 pixels per side.
- The numeric codes scramble the hidden image data; this app does not encrypt it. Block scrambling keeps the overall colors and textures of the image, so a determined person could try to reassemble the blocks. Do not use it as a replacement for encryption when you need strong confidentiality.

## Command line

```
python -m image_merger.main merge --image-a a.jpg --code-a 12345 --image-b b.png --code-b 67890 \
    --carrier carrier.jpg --output out.png --quality 80 --carrier-max-side 1920
python -m image_merger.main extract out.png recovered.png --code 12345
```

Extra merge options: `--quality 1-100|lossless`, `--block-size 16|8`, `--carrier-format png|jpeg`, `--carrier-max-side PX`, `--carrier-256-colors`, `--carrier-jpeg-quality 50-100`. With `--carrier-format jpeg` the output name must end in `.jpg`.

## Troubleshooting

**The app says the file has no hidden image data.** The file may not have been created by Image Merger, or it may have been changed or recompressed after merging (for example by a messaging app). Try the original merged file.

**The recovered image does not look right.** Check that you entered the same code used when that image was added. Remember that leading zeros are ignored when comparing codes. A wrong code always shows a scrambled image instead of an error.

**The app says WebP support is missing.** Reinstall or update the app; WebP support must be bundled with Pillow.
