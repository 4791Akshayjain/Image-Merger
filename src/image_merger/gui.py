import os
import threading
import tkinter as tk
from tkinter import filedialog, messagebox

from PIL import ImageOps, Image, ImageTk
from tkinterdnd2 import DND_FILES, TkinterDnD

from image_merger.format import ImageMergerError
from image_merger.image_pipeline import create_merged_png, extract_image, validate_merge_codes
from image_merger.shuffle import MAX_CODE_DIGITS, MIN_CODE_DIGITS

IMAGE_TYPES = [("Image files", "*.png *.jpg *.jpeg *.bmp *.gif *.webp *.tif *.tiff"),
               ("All files", "*.*")]
PNG_TYPES = [("PNG image", "*.png"), ("All files", "*.*")]
BG = "#f0f0f0"
OK_COLOR = "#1a7a1a"
ERR_COLOR = "#c0392b"


class DropZone(tk.Frame):
    """A drag-and-drop / click-to-browse file box."""

    def __init__(self, parent, prompt, filetypes, title, on_change=None, width=440, height=64):
        super().__init__(parent, width=width, height=height, bg=BG,
                         highlightbackground="#999", highlightthickness=2)
        self.pack_propagate(False)
        self.prompt = prompt
        self.filetypes = filetypes
        self.title = title
        self.on_change = on_change
        self.path = None

        self.label = tk.Label(self, text=prompt, bg=BG, fg="#555",
                              justify="center", wraplength=width - 20)
        self.label.pack(expand=True)

        for widget in (self, self.label):
            widget.bind("<Button-1>", lambda e: self.browse())
            widget.drop_target_register(DND_FILES)
            widget.dnd_bind("<<Drop>>", self._on_drop)

    def _on_drop(self, event):
        paths = self.tk.splitlist(event.data)  # handles {paths with spaces}
        if paths:
            self.set_path(paths[0])

    def browse(self):
        path = filedialog.askopenfilename(title=self.title, filetypes=self.filetypes)
        if path:
            self.set_path(path)

    def set_path(self, path):
        self.path = path
        self.label.config(text=os.path.basename(path), fg="#1a1a1a")
        if self.on_change:
            self.on_change(path)


class ImageMergerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Image Merger")
        self.root.geometry("500x700")
        self.root.resizable(False, False)

        self.mode = tk.StringVar(value="merge")
        self._vcmd = (self.root.register(self._validate_digits), "%P")
        self._photo_refs = {}      # keep PhotoImage objects alive
        self.result_image = None   # last extracted PIL image

        self._build_ui()
        self._on_mode_change()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_ui(self):
        mode_frame = tk.Frame(self.root, pady=10)
        mode_frame.pack(fill="x")
        tk.Radiobutton(mode_frame, text="Merge images", variable=self.mode,
                       value="merge", command=self._on_mode_change).pack(side="left", padx=(90, 10))
        tk.Radiobutton(mode_frame, text="Extract image", variable=self.mode,
                       value="extract", command=self._on_mode_change).pack(side="left")

        self.content = tk.Frame(self.root)
        self.content.pack(fill="both", expand=True, padx=30)

        self.merge_frame = self._build_merge_frame(self.content)
        self.extract_frame = self._build_extract_frame(self.content)

        self.status_label = tk.Label(self.root, text="", wraplength=440, justify="center")
        self.status_label.pack(pady=8)

    def _make_code_entry(self, parent, label):
        tk.Label(parent, text=label).pack(anchor="w")
        var = tk.StringVar()
        entry = tk.Entry(parent, textvariable=var, validate="key", validatecommand=self._vcmd)
        entry.pack(fill="x", pady=(0, 6))
        return var

    def _build_merge_frame(self, parent):
        f = tk.Frame(parent)

        self.zone_a = DropZone(f, "Image A\n(drop here or click to browse)",
                               IMAGE_TYPES, "Select Image A")
        self.zone_a.pack(pady=(0, 4))
        self.code_a = self._make_code_entry(
            f, f"Code for Image A ({MIN_CODE_DIGITS}-{MAX_CODE_DIGITS} digits):")

        self.zone_b = DropZone(f, "Image B\n(drop here or click to browse)",
                               IMAGE_TYPES, "Select Image B")
        self.zone_b.pack(pady=(6, 4))
        self.code_b = self._make_code_entry(
            f, f"Code for Image B ({MIN_CODE_DIGITS}-{MAX_CODE_DIGITS} digits, different):")

        row = tk.Frame(f)
        row.pack(fill="x", pady=6)
        self.zone_carrier = DropZone(
            row, "Carrier image\n(what everyone sees)", IMAGE_TYPES, "Select carrier image",
            on_change=self._update_carrier_preview, width=290, height=100)
        self.zone_carrier.pack(side="left")

        preview_box = tk.Frame(row, width=140, height=100, bg="#fff",
                               highlightbackground="#999", highlightthickness=1)
        preview_box.pack(side="right")
        preview_box.pack_propagate(False)
        self.carrier_preview = tk.Label(preview_box, text="Preview", bg="#fff", fg="#999")
        self.carrier_preview.pack(expand=True)

        self.merge_btn = tk.Button(f, text="Create merged PNG...", width=24, bg="#4a90d9",
                                   fg="white", activebackground="#3a78bd", command=self._on_merge)
        self.merge_btn.pack(pady=12)
        return f

    def _build_extract_frame(self, parent):
        f = tk.Frame(parent)

        self.zone_merged = DropZone(f, "Merged PNG\n(drop here or click to browse)",
                                    PNG_TYPES, "Select merged PNG",
                                    on_change=lambda p: self._clear_result())
        self.zone_merged.pack(pady=(0, 8))
        self.extract_code = self._make_code_entry(f, "Code of the image to recover:")

        self.extract_btn = tk.Button(f, text="Extract", width=24, bg="#4a90d9", fg="white",
                                     activebackground="#3a78bd", command=self._on_extract)
        self.extract_btn.pack(pady=8)

        result_box = tk.Frame(f, width=440, height=250, bg="#fff",
                              highlightbackground="#999", highlightthickness=1)
        result_box.pack()
        result_box.pack_propagate(False)
        self.result_preview = tk.Label(result_box, text="Result will appear here",
                                       bg="#fff", fg="#999")
        self.result_preview.pack(expand=True)

        self.save_btn = tk.Button(f, text="Save As PNG...", width=24, state="disabled",
                                  command=self._on_save)
        self.save_btn.pack(pady=10)
        return f

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _validate_digits(self, proposed):
        return proposed == "" or (proposed.isascii() and proposed.isdigit()
                                  and len(proposed) <= MAX_CODE_DIGITS)

    def _on_mode_change(self):
        self.status_label.config(text="")
        if self.mode.get() == "merge":
            self.extract_frame.pack_forget()
            self.merge_frame.pack(fill="both", expand=True)
        else:
            self.merge_frame.pack_forget()
            self.extract_frame.pack(fill="both", expand=True)

    def _set_status(self, text, color="#333"):
        self.status_label.config(text=text, fg=color)

    def _show_error(self, exc):
        if isinstance(exc, ImageMergerError):
            message = str(exc)
        elif isinstance(exc, MemoryError):
            message = "Not enough memory to process these images. Try smaller images."
        else:
            message = f"Unexpected error: {exc}"
        self._set_status(f"Error: {message}", ERR_COLOR)
        messagebox.showerror("Something went wrong", message)

    def _set_busy(self, busy, text=""):
        state = "disabled" if busy else "normal"
        self.merge_btn.config(state=state)
        self.extract_btn.config(state=state)
        if busy:
            self._set_status(text)

    def _run_async(self, work, on_success, busy_text):
        """Run `work` in a thread so the window stays responsive."""
        self._set_busy(True, busy_text)
        outcome = {}

        def target():
            try:
                outcome["value"] = work()
            except Exception as e:  # reported on the UI thread below
                outcome["error"] = e

        thread = threading.Thread(target=target, daemon=True)
        thread.start()

        def poll():
            if thread.is_alive():
                self.root.after(100, poll)
                return
            self._set_busy(False)
            if "error" in outcome:
                self._show_error(outcome["error"])
            else:
                on_success(outcome["value"])

        self.root.after(100, poll)

    def _thumbnail(self, pil_image, size, key):
        thumb = pil_image.copy()
        thumb.thumbnail(size)
        photo = ImageTk.PhotoImage(thumb)
        self._photo_refs[key] = photo
        return photo

    # ------------------------------------------------------------------
    # Merge
    # ------------------------------------------------------------------
    def _update_carrier_preview(self, path):
        try:
            with Image.open(path) as img:
                img = ImageOps.exif_transpose(img)
                photo = self._thumbnail(img.convert("RGBA"), (136, 96), "carrier")
            self.carrier_preview.config(image=photo, text="")
        except Exception:
            self._photo_refs.pop("carrier", None)
            self.carrier_preview.config(image="", text="No preview")

    def _on_merge(self):
        a, b, carrier = self.zone_a.path, self.zone_b.path, self.zone_carrier.path
        if not (a and b and carrier):
            messagebox.showwarning("Missing files",
                                   "Please choose Image A, Image B and a carrier image.")
            return

        code_a, code_b = self.code_a.get(), self.code_b.get()
        try:
            validate_merge_codes(code_a, code_b)
        except ImageMergerError as e:
            messagebox.showwarning("Check the codes", str(e))
            return

        confirmed = messagebox.askyesno(
            "Confirm inputs",
            f"Image A:  {os.path.basename(a)}\n"
            f"Image B:  {os.path.basename(b)}\n"
            f"Carrier:  {os.path.basename(carrier)}\n\n"
            "The carrier is the picture everyone will see. Create the merged PNG?")
        if not confirmed:
            return

        base = os.path.splitext(os.path.basename(carrier))[0]
        out_path = filedialog.asksaveasfilename(
            title="Save merged PNG", defaultextension=".png",
            initialfile=f"{base}_merged.png", filetypes=[("PNG image", "*.png")])
        if not out_path:
            return

        norm = lambda p: os.path.normcase(os.path.abspath(p))
        if norm(out_path) in {norm(a), norm(b), norm(carrier)}:
            messagebox.showerror("Choose another name",
                                 "Don't overwrite one of the input files. Pick a new file name.")
            return

        def done(path):
            size_mb = os.path.getsize(path) / (1024 * 1024)
            self._set_status(f"Saved: {path}  ({size_mb:.1f} MB)", OK_COLOR)

        self._run_async(lambda: create_merged_png(a, code_a, b, code_b, carrier, out_path),
                        done, "Merging... this can take a few seconds for large images.")

    # ------------------------------------------------------------------
    # Extract
    # ------------------------------------------------------------------
    def _clear_result(self):
        self.result_image = None
        self._photo_refs.pop("result", None)
        self.result_preview.config(image="", text="Result will appear here")
        self.save_btn.config(state="disabled")
        self._set_status("")

    def _on_extract(self):
        path, code = self.zone_merged.path, self.extract_code.get()
        if not path:
            messagebox.showwarning("No file", "Please choose a merged PNG first.")
            return
        if not code:
            messagebox.showwarning("No code", "Please enter a code.")
            return

        self._clear_result()
        self._run_async(lambda: extract_image(path, code), self._show_extracted,
                        "Extracting...")

    def _show_extracted(self, result):
        # Deliberately ignore result.matched_index: wrong codes must look the same.
        self.result_image = result.image
        photo = self._thumbnail(result.image, (430, 240), "result")
        self.result_preview.config(image=photo, text="")
        self.save_btn.config(state="normal")
        self._set_status("Done. Click 'Save As PNG...' to keep the image.", OK_COLOR)

    def _on_save(self):
        if self.result_image is None:
            return
        base = os.path.splitext(os.path.basename(self.zone_merged.path or "image"))[0]
        path = filedialog.asksaveasfilename(
            title="Save extracted image", defaultextension=".png",
            initialfile=f"{base}_extracted.png", filetypes=[("PNG image", "*.png")])
        if not path:
            return
        try:
            self.result_image.save(path, format="PNG")
            self._set_status(f"Saved: {path}", OK_COLOR)
        except OSError as e:
            self._show_error(ImageMergerError(f"Could not save the image: {e}"))


def main():
    root = TkinterDnD.Tk()
    ImageMergerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
