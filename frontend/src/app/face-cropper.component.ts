import {CommonModule} from '@angular/common';
import {Component, EventEmitter, Input, OnChanges, OnDestroy, Output, signal} from '@angular/core';
import {FormsModule} from '@angular/forms';

interface CropArea {x: number; y: number; width: number; height: number;}

@Component({
  selector: 'app-face-cropper', standalone: true, imports: [CommonModule, FormsModule],
  template: `
    <div class="face-cropper">
      <h3>Crop Face Region</h3>
      <p>Select an area containing the entire face with a small margin. Drag inside the area to move it, or use the bottom-right handle to resize it.</p>
      @if (loading()) {<p role="status">Loading image.</p>}
      @if (error()) {<div class="alert alert-danger" role="alert">{{ error() }}</div>}
      @if (image()) {
        <div class="crop-layout">
          <div class="crop-stage" [style.width.px]="stageWidth()"
            (pointerdown)="beginCrop($event)" (pointermove)="moveCrop($event)"
            (pointerup)="endCrop($event)" (pointercancel)="endCrop($event)">
            <img [src]="sourceUrl()" alt="Source image for selecting a face region" draggable="false">
            <div class="crop-selection" [style.left.%]="area.x / width * 100" [style.top.%]="area.y / height * 100"
              [style.width.%]="area.width / width * 100" [style.height.%]="area.height / height * 100">
              <span class="crop-selection-label">Face Region</span>
              <button type="button" class="crop-handle" aria-label="Resize crop region" [disabled]="locked()"></button>
            </div>
          </div>
          <div class="crop-controls">
            <h4>Crop Preview</h4><img class="crop-preview" [src]="preview()" alt="Preview of the cropped image to save">
            <div class="crop-coordinates">
              <label>Start X<input aria-label="Crop Origin X" type="number" class="form-control" [ngModel]="area.x" (ngModelChange)="adjust('x', $event)" [min]="0" [max]="width - area.width" [disabled]="locked()"></label>
              <label>Start Y<input aria-label="Crop Origin Y" type="number" class="form-control" [ngModel]="area.y" (ngModelChange)="adjust('y', $event)" [min]="0" [max]="height - area.height" [disabled]="locked()"></label>
              <label>Width<input aria-label="Crop Width" type="number" class="form-control" [ngModel]="area.width" (ngModelChange)="adjust('width', $event)" [min]="80" [max]="width - area.x" [disabled]="locked()"></label>
              <label>Height<input aria-label="Crop Height" type="number" class="form-control" [ngModel]="area.height" (ngModelChange)="adjust('height', $event)" [min]="80" [max]="height - area.y" [disabled]="locked()"></label>
            </div>
            <small>{{ area.width }} × {{ area.height }} px · Image {{ width }} × {{ height }} px</small>
            <button type="button" class="btn btn-outline-secondary" (click)="resetCrop()" [disabled]="locked()">Select Full Image</button>
          </div>
        </div>
      }
      <div class="crop-actions">
        <button type="button" class="btn btn-primary" (click)="save()" [disabled]="locked() || !image()">{{ locked() ? 'Processing…' : 'Save Cropped Face' }}</button>
        <button type="button" class="btn btn-outline-secondary" (click)="skipCrop.emit()" [disabled]="locked()">Cancel This Image</button>
      </div>
      <small>Saving the selected area registers an aligned face image after detection and quality checks.</small>
    </div>
  `
})
export class FaceCropperComponent implements OnChanges, OnDestroy {
  @Input({required: true}) file!: File;
  @Input() disabled = false;
  @Output() saveCrop = new EventEmitter<Blob>();
  @Output() skipCrop = new EventEmitter<void>();
  image = signal<HTMLImageElement | null>(null);
  sourceUrl = signal('');
  preview = signal('');
  loading = signal(false);
  exporting = signal(false);
  error = signal('');
  width = 0; height = 0;
  area: CropArea = {x: 0, y: 0, width: 0, height: 0};
  private version = 0;
  private drag: {id: number; mode: 'move' | 'resize' | 'draw'; x: number; y: number; area: CropArea} | null = null;

  ngOnChanges(changes: {[key: string]: unknown}) {
    if (changes['file']) this.loadImage();
  }
  ngOnDestroy() { this.version++; this.releaseImage(); }
  locked() {return this.disabled || this.exporting();}
  stageWidth() {return Math.min(640, 640 * this.width / this.height);}
  private releaseImage() {
    if (this.sourceUrl()) URL.revokeObjectURL(this.sourceUrl());
    this.sourceUrl.set(''); this.image.set(null); this.preview.set(''); this.drag = null;
  }
  private loadImage() {
    const version = ++this.version;
    this.releaseImage(); this.loading.set(true); this.exporting.set(false); this.error.set('');
    if (!['image/jpeg', 'image/png'].includes(this.file.type) || this.file.size > 10 * 1024 ** 2) {
      this.loading.set(false); this.error.set('Choose a JPEG or PNG image up to 10 MB.'); return;
    }
    const image = new Image(), url = URL.createObjectURL(this.file);
    this.sourceUrl.set(url);
    image.onload = () => {
      if (version !== this.version) return;
      this.loading.set(false);
      this.width = image.naturalWidth; this.height = image.naturalHeight;
      if (this.width < 80 || this.height < 80 || this.width > 4096 || this.height > 4096 || this.width * this.height > 12_000_000) {
        this.releaseImage(); this.error.set('Choose an image with sides from 80 to 4096 px and no more than 12 million pixels.'); return;
      }
      this.image.set(image); this.area = {x: 0, y: 0, width: this.width, height: this.height}; this.updatePreview();
    };
    image.onerror = () => {
      if (version !== this.version) return;
      this.loading.set(false); this.releaseImage(); this.error.set('Could not read the image. Choose another photo.');
    };
    image.src = url;
  }
  resetCrop() {
    if (this.locked()) return;
    this.area = {x: 0, y: 0, width: this.width, height: this.height}; this.updatePreview();
  }
  adjust(key: keyof CropArea, value: number) {
    if (this.locked() || !Number.isFinite(Number(value))) return;
    this.area = {...this.area, [key]: Math.round(Number(value))}; this.normalize(); this.updatePreview();
  }
  private normalize() {
    const clamp = (value: number, low: number, high: number) => Math.max(low, Math.min(value, high));
    this.area.width = clamp(Math.round(this.area.width), 80, this.width);
    this.area.height = clamp(Math.round(this.area.height), 80, this.height);
    this.area.x = clamp(Math.round(this.area.x), 0, this.width - this.area.width);
    this.area.y = clamp(Math.round(this.area.y), 0, this.height - this.area.height);
  }
  private point(event: PointerEvent) {
    const bounds = (event.currentTarget as HTMLElement).getBoundingClientRect();
    return {x: Math.max(0, Math.min(this.width, (event.clientX - bounds.left) * this.width / bounds.width)),
      y: Math.max(0, Math.min(this.height, (event.clientY - bounds.top) * this.height / bounds.height))};
  }
  beginCrop(event: PointerEvent) {
    if (this.locked() || !this.image() || event.button !== 0 || this.drag) return;
    event.preventDefault();
    const point = this.point(event), target = event.target as HTMLElement;
    const full = this.area.width === this.width && this.area.height === this.height;
    const mode = target.closest('.crop-handle') ? 'resize' : target.closest('.crop-selection') && !full ? 'move' : 'draw';
    this.drag = {id: event.pointerId, mode, ...point, area: {...this.area}};
    (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
  }
  moveCrop(event: PointerEvent) {
    if (this.locked() || !this.drag || event.pointerId !== this.drag.id) return;
    const point = this.point(event), drag = this.drag;
    const dx = point.x - drag.x, dy = point.y - drag.y;
    if (drag.mode === 'move') this.area = {...drag.area, x: drag.area.x + dx, y: drag.area.y + dy};
    else if (drag.mode === 'resize') this.area = {...drag.area,
      width: Math.min(this.width - drag.area.x, drag.area.width + dx),
      height: Math.min(this.height - drag.area.y, drag.area.height + dy)};
    else this.area = {x: Math.min(point.x, drag.x), y: Math.min(point.y, drag.y), width: Math.abs(dx), height: Math.abs(dy)};
    this.normalize(); this.updatePreview();
  }
  endCrop(event: PointerEvent) {
    if (this.drag?.id !== event.pointerId) return;
    const stage = event.currentTarget as HTMLElement;
    if (stage.hasPointerCapture(event.pointerId)) stage.releasePointerCapture(event.pointerId);
    this.drag = null;
  }
  private canvas(fullSize: boolean): HTMLCanvasElement {
    const canvas = document.createElement('canvas');
    const scale = fullSize ? 1 : Math.min(1, 240 / Math.max(this.area.width, this.area.height));
    canvas.width = Math.max(1, Math.round(this.area.width * scale));
    canvas.height = Math.max(1, Math.round(this.area.height * scale));
    canvas.getContext('2d')!.drawImage(this.image()!, this.area.x, this.area.y, this.area.width, this.area.height, 0, 0, canvas.width, canvas.height);
    return canvas;
  }
  private updatePreview() {if (this.image()) this.preview.set(this.canvas(false).toDataURL('image/jpeg', 0.9));}
  save() {
    if (this.locked() || !this.image()) return;
    this.error.set(''); this.exporting.set(true);
    const version = this.version;
    this.canvas(true).toBlob(blob => {
      if (version !== this.version) return;
      this.exporting.set(false);
      if (!blob || blob.size > 10 * 1024 ** 2) {this.error.set('Could not create the crop, or it exceeded 10 MB. Reduce the selected area.'); return;}
      this.saveCrop.emit(blob);
    }, 'image/jpeg', 0.95);
  }
}
