import {CommonModule} from '@angular/common';
import {HttpClient, HttpErrorResponse, HttpEventType} from '@angular/common/http';
import {Component, DestroyRef, ElementRef, EventEmitter, Input, OnChanges, OnDestroy, Output, ViewChild, computed, inject, signal} from '@angular/core';
import {takeUntilDestroyed} from '@angular/core/rxjs-interop';
import {timeout} from 'rxjs';

interface FaceGroup {
  group_id: number; occurrences: number; first_seen_seconds: number; last_seen_seconds: number;
  best_frame: number; best_seen_seconds: number; embedding_ready: boolean;
}
interface FaceJob {
  job_id: string; filename: string; state: string; error_code: string | null;
  processed_frames: number; total_frames: number | null; detections: number;
  progress_percent: number | null; threshold: number; actual_device: string; groups: FaceGroup[];
}
interface FaceTests {
  items: FaceJob[]; can_start: boolean;
  limits: {upload_max_mb: number; retention_hours: number; max_duration_seconds: number};
}

@Component({selector:'app-face-test', standalone:true, imports:[CommonModule], templateUrl:'./face-test.component.html'})
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
  private version = 0;
  private poll = window.setInterval(() => {
    if (this.result()?.items.some(job => this.active(job)) || this.result()?.can_start === false) this.load(false);
  }, 1500);

  ngOnChanges() {this.load();}
  ngOnDestroy() {window.clearInterval(this.poll);}
  hasActiveJob() {return this.result()?.items.some(job => this.active(job)) ?? false;}
  active(job: FaceJob) {return job.state === 'queued' || job.state === 'running';}
  selectVideo(event: Event) {
    this.error.set(''); this.notice.set('');
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0] ?? null;
    if (file && file.size > (this.result()?.limits.upload_max_mb ?? 200) * 1024 * 1024) {
      this.error.set(`영상은 ${this.result()?.limits.upload_max_mb ?? 200} MB 이하로 선택해 주세요.`);
      this.selectedFile.set(null); input.value = ''; return;
    }
    if (file && !file.size) {this.error.set('빈 파일은 분석할 수 없습니다.'); this.selectedFile.set(null); input.value = ''; return;}
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
        if (!result.items.some(job => job.job_id === this.selectedId())) this.selectedId.set(result.items[0]?.job_id ?? '');
      }, error: error => {if (version === this.version) {this.loading.set(false); this.failed(error);}}
    });
  }
  analyze() {
    const file = this.selectedFile();
    if (!file || !this.result()?.can_start || this.uploading() || this.deleting()) return;
    ++this.version; this.loading.set(false);
    this.uploading.set(true); this.uploadPercent.set(null); this.savingChange.emit(true);
    this.error.set(''); this.notice.set('');
    this.http.post<FaceJob>('/api/face-tests', file, {
      headers:{...this.requestHeaders, 'Content-Type':file.type.startsWith('video/') ? file.type : 'application/octet-stream'},
      params:{filename:file.name.slice(0,200)}, observe:'events', reportProgress:true
    }).pipe(timeout(120000), takeUntilDestroyed(this.destroyRef)).subscribe({
      next: event => {
        if (event.type === HttpEventType.UploadProgress && event.total) this.uploadPercent.set(Math.round(event.loaded / event.total * 100));
        if (event.type === HttpEventType.Response && event.body) {
          const job = event.body;
          this.result.update(result => result ? {...result, can_start:false, items:[job, ...result.items]} : result);
          this.selectedId.set(job.job_id); this.uploading.set(false); this.savingChange.emit(false);
          this.notice.set('영상을 업로드했습니다. 모든 프레임을 순서대로 분석합니다.');
          this.load(false);
        }
      }, error: error => {this.uploading.set(false); this.savingChange.emit(false); this.failed(error);}
    });
  }
  deleteAll() {
    if (!this.result()?.items.length || this.uploading() || this.deleting()) return;
    if (!window.confirm('분석 중인 작업을 중지하고 모든 분석 목록과 추출 사진을 삭제할까요?')) return;
    ++this.version; this.loading.set(false); this.deleting.set(true); this.savingChange.emit(true); this.error.set(''); this.notice.set('');
    this.http.delete('/api/face-tests', {headers:this.requestHeaders}).pipe(timeout(35000), takeUntilDestroyed(this.destroyRef)).subscribe({
      next: () => {
        this.result.update(result => result ? {...result, items:[], can_start:true} : result);
        this.selectedId.set(''); this.selectedFile.set(null);
        if (this.videoInput) this.videoInput.nativeElement.value = '';
        this.deleting.set(false); this.savingChange.emit(false);
        this.notice.set('모든 분석 목록과 추출 사진을 삭제했습니다.'); this.load(false);
      }, error: error => {this.deleting.set(false); this.savingChange.emit(false); this.failed(error);}
    });
  }
  imageUrl(job: FaceJob, group: FaceGroup) {return `/api/face-tests/${job.job_id}/groups/${group.group_id}/image?frame=${group.best_frame}`;}
  stateLabel(state: string) {return ({queued:'분석 준비 중', running:'영상 분석 중', completed:'분석 완료', failed:'분석 실패', cancelled:'분석 중지'} as Record<string,string>)[state] ?? state;}
  errorLabel(code: string | null) {
    return ({invalid_video:'영상을 읽을 수 없습니다. 지원되는 영상 파일과 해상도(최대 3840×2160)를 확인해 주세요.',
      duration_limit_exceeded:'영상 길이 또는 프레임 수 제한을 초과했습니다. 더 짧은 영상으로 시험해 주세요.',
      face_limit_exceeded:'한 프레임의 얼굴 수가 처리 제한을 초과했습니다.', group_limit_exceeded:'추출 인물 수가 처리 제한을 초과했습니다.',
      storage_limit_exceeded:'시험 영상 저장 공간이 부족합니다. 목록을 삭제한 후 다시 분석해 주세요.',
      video_decode_incomplete:'영상 일부를 읽지 못해 분석을 완료하지 못했습니다.', worker_restarted:'분석 서비스가 재시작되어 분석이 중단되었습니다.',
      cancelled:'분석이 중지되었습니다.'} as Record<string,string>)[code ?? ''] ?? '분석을 완료하지 못했습니다. 영상 파일과 분석 서비스 상태를 확인해 주세요.';
  }
  private failed(error: HttpErrorResponse) {
    if (error.status === 401) this.sessionExpired.emit();
    this.error.set(error.status === 413 ? '영상 파일이 업로드 용량 제한을 초과했습니다.'
      : error.status === 429 ? '다른 영상이 분석 중이거나 저장 제한에 도달했습니다. 완료를 기다리거나 목록을 삭제해 주세요.'
      : error.status === 422 || error.status === 415 ? '지원되는 영상 파일을 선택해 주세요.'
      : '요청을 처리하지 못했습니다. 서버 연결을 확인한 후 새로고침해 주세요.');
  }
}
