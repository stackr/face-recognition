import {CommonModule} from '@angular/common';
import {HttpClient, HttpErrorResponse, HttpEventType} from '@angular/common/http';
import {Component, DestroyRef, ElementRef, EventEmitter, Input, OnChanges, OnDestroy, Output, ViewChild, computed, inject, signal} from '@angular/core';
import {takeUntilDestroyed} from '@angular/core/rxjs-interop';
import {FormsModule} from '@angular/forms';
import {timeout} from 'rxjs';

interface FaceGroup {
  group_id: number; occurrences: number; first_seen_seconds: number; last_seen_seconds: number;
  best_frame: number; best_seen_seconds: number; embedding_ready: boolean;
}
interface FaceJob {
  job_id: string; filename: string; state: string; error_code: string | null;
  processed_frames: number; total_frames: number | null; detections: number;
  progress_percent: number | null; threshold: number; actual_device: string; groups: FaceGroup[];
  detection_threshold?: number; min_face_size?: number;
  groups_before_merge?: number | null; merged_group_count?: number;
}
interface FaceTests {
  items: FaceJob[]; can_start: boolean;
  limits: {upload_max_mb: number; retention_hours: number; max_duration_seconds: number};
  defaults: {detection_threshold: number; min_face_size: number; match_threshold: number};
}

@Component({selector:'app-face-test', standalone:true, imports:[CommonModule, FormsModule], templateUrl:'./face-test.component.html'})
export class FaceTestComponent implements OnChanges, OnDestroy {
  private http = inject(HttpClient);
  private destroyRef = inject(DestroyRef);
  @Input() requestHeaders: Record<string, string> = {};
  @Input() refreshVersion = 0;
  @Output() sessionExpired = new EventEmitter<void>();
  @Output() savingChange = new EventEmitter<boolean>();
  @ViewChild('videoInput') videoInput?: ElementRef<HTMLInputElement>;
  result = signal<FaceTests | null>(null);
  selectedId = signal('');
  selectedFile = signal<File | null>(null);
  current = computed(() => this.result()?.items.find(job => job.job_id === this.selectedId()) ?? null);
  uploading = signal(false);
  uploadPercent = signal<number | null>(null);
  deleting = signal(false);
  loading = signal(false);
  error = signal('');
  notice = signal('');
  detectionThreshold: number | null = 0.5;
  minFaceSize: number | null = 8;
  matchThreshold: number | null = 0.75;
  private settingsInitialized = false;
  private version = 0;
  private poll = window.setInterval(() => {
    if (this.result()?.items.some(job => this.active(job)) || this.result()?.can_start === false) this.load(false);
  }, 1500);

  ngOnChanges() {this.load();}
  ngOnDestroy() {window.clearInterval(this.poll);}
  hasActiveJob() {return this.result()?.items.some(job => this.active(job)) ?? false;}
  active(job: FaceJob) {return ['queued', 'running', 'merging'].includes(job.state);}
  validSettings() {
    return typeof this.detectionThreshold === 'number' && Number.isFinite(this.detectionThreshold)
      && this.detectionThreshold >= 0.1 && this.detectionThreshold <= 0.99
      && typeof this.minFaceSize === 'number' && Number.isInteger(this.minFaceSize)
      && this.minFaceSize >= 8 && this.minFaceSize <= 512
      && typeof this.matchThreshold === 'number' && Number.isFinite(this.matchThreshold)
      && this.matchThreshold >= -1 && this.matchThreshold <= 1;
  }
  selectVideo(event: Event) {
    this.error.set(''); this.notice.set('');
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0] ?? null;
    if (file && file.size > (this.result()?.limits.upload_max_mb ?? 200) * 1024 * 1024) {
      this.error.set(`Choose a video up to ${this.result()?.limits.upload_max_mb ?? 200} MB.`);
      this.selectedFile.set(null); input.value = ''; return;
    }
    if (file && !file.size) {this.error.set('Empty files cannot be analyzed.'); this.selectedFile.set(null); input.value = ''; return;}
    this.selectedFile.set(file);
  }
  selectJob(event: Event) {this.selectedId.set((event.target as HTMLSelectElement).value);}
  load(showLoading = true) {
    if (this.uploading() || this.deleting() || this.loading()) return;
    const version = ++this.version;
    this.loading.set(true);
    if (showLoading) this.error.set('');
    this.http.get<FaceTests>('/api/face-tests').pipe(timeout(12000), takeUntilDestroyed(this.destroyRef)).subscribe({
      next: result => {
        if (version !== this.version) return;
        this.result.set(result); this.loading.set(false);
        if (!this.settingsInitialized) {
          this.detectionThreshold = result.items[0]?.detection_threshold ?? result.defaults.detection_threshold;
          this.minFaceSize = result.items[0]?.min_face_size ?? result.defaults.min_face_size;
          this.matchThreshold = result.items[0]?.threshold ?? result.defaults.match_threshold;
          this.settingsInitialized = true;
        }
        if (!result.items.some(job => job.job_id === this.selectedId())) this.selectedId.set(result.items[0]?.job_id ?? '');
      }, error: error => {if (version === this.version) {this.loading.set(false); this.failed(error);}}
    });
  }
  analyze() {
    const file = this.selectedFile();
    if (!file || !this.validSettings() || !this.result()?.can_start || this.uploading() || this.deleting()) return;
    ++this.version; this.loading.set(false);
    this.uploading.set(true); this.uploadPercent.set(null); this.savingChange.emit(true);
    this.error.set(''); this.notice.set('');
    this.http.post<FaceJob>('/api/face-tests', file, {
      headers:{...this.requestHeaders, 'Content-Type':file.type.startsWith('video/') ? file.type : 'application/octet-stream'},
      params:{filename:file.name.slice(0,200), detection_threshold:this.detectionThreshold!, min_face_size:this.minFaceSize!, match_threshold:this.matchThreshold!}, observe:'events', reportProgress:true
    }).pipe(timeout(120000), takeUntilDestroyed(this.destroyRef)).subscribe({
      next: event => {
        if (event.type === HttpEventType.UploadProgress && event.total) this.uploadPercent.set(Math.round(event.loaded / event.total * 100));
        if (event.type === HttpEventType.Response && event.body) {
          const job = event.body;
          this.result.update(result => result ? {...result, can_start:false, items:[job, ...result.items]} : result);
          this.selectedId.set(job.job_id); this.uploading.set(false); this.savingChange.emit(false);
          this.notice.set('Video uploaded. Analyzing all frames sequentially.');
          this.load(false);
        }
      }, error: error => {this.uploading.set(false); this.savingChange.emit(false); this.failed(error);}
    });
  }
  deleteAll() {
    if (!this.result()?.items.length || this.uploading() || this.deleting()) return;
    if (!window.confirm('Stop active analysis and delete all analysis results and extracted images?')) return;
    ++this.version; this.loading.set(false); this.deleting.set(true); this.savingChange.emit(true); this.error.set(''); this.notice.set('');
    this.http.delete('/api/face-tests', {headers:this.requestHeaders}).pipe(timeout(35000), takeUntilDestroyed(this.destroyRef)).subscribe({
      next: () => {
        this.result.update(result => result ? {...result, items:[], can_start:true} : result);
        this.selectedId.set(''); this.selectedFile.set(null);
        if (this.videoInput) this.videoInput.nativeElement.value = '';
        this.deleting.set(false); this.savingChange.emit(false);
        this.notice.set('All analysis results and extracted images deleted.'); this.load(false);
      }, error: error => {this.deleting.set(false); this.savingChange.emit(false); this.failed(error);}
    });
  }
  imageUrl(job: FaceJob, group: FaceGroup) {return `/api/face-tests/${job.job_id}/groups/${group.group_id}/image?frame=${group.best_frame}`;}
  stateLabel(state: string) {return ({queued:'Preparing analysis', running:'Analyzing video', merging:'Merging face groups', completed:'Analysis complete', failed:'Analysis failed', cancelled:'Analysis stopped'} as Record<string,string>)[state] ?? state;}
  errorLabel(code: string | null) {
    return ({invalid_video:'Could not read the video. Check the file format and resolution (maximum 3840×2160).',
      duration_limit_exceeded:'Video duration or frame-count limit exceeded. Try a shorter video.',
      face_limit_exceeded:'The number of faces in one frame exceeded the processing limit.', group_limit_exceeded:'The number of extracted people exceeded the processing limit.',
      storage_limit_exceeded:'Test-video storage is full. Delete results before analyzing again.',
      video_decode_incomplete:'Analysis could not finish because part of the video was unreadable.', worker_restarted:'Analysis stopped because the analysis service restarted.',
      cancelled:'Analysis stopped.'} as Record<string,string>)[code ?? ''] ?? 'Analysis could not be completed. Check the video file and analysis service.';
  }
  private failed(error: HttpErrorResponse) {
    if (error.status === 401) this.sessionExpired.emit();
    this.error.set(error.status === 413 ? 'Video exceeds the upload size limit.'
      : error.status === 429 ? 'Another video is being analyzed or the storage limit has been reached. Wait for completion or delete the results.'
      : error.status === 422 ? 'Check the video file, detection threshold, minimum face size and similarity threshold.'
      : error.status === 415 ? 'Select a supported video file.'
      : 'Could not process the request. Check the server connection and refresh.');
  }
}
