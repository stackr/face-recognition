import {CommonModule} from '@angular/common';
import {HttpClient, HttpErrorResponse} from '@angular/common/http';
import {Component, DestroyRef, EventEmitter, Input, OnChanges, OnDestroy, Output, SimpleChanges, inject, signal} from '@angular/core';
import {takeUntilDestroyed} from '@angular/core/rxjs-interop';
import {FormsModule, NgForm} from '@angular/forms';
import {timeout} from 'rxjs';

interface SamplingValues {detection_fps: number; face_analysis_interval: number; face_rois_per_frame: number; face_match_threshold: number; detection_confidence: number; person_detection_enabled: boolean; video_face_detection_threshold: number; video_face_min_size: number;}
interface SettingsResult {
  revision: number; values: SamplingValues; applied: boolean;
  active: {revision: number; values: SamplingValues} | null;
  features: {sample_count: number; sample_window_seconds: number; minimum_samples: number; retry_detector_size: number; person_track_start_threshold: number};
}

@Component({selector: 'app-function-settings', standalone: true, imports: [CommonModule, FormsModule], templateUrl: './function-settings.component.html'})
export class FunctionSettingsComponent implements OnChanges, OnDestroy {
  private http = inject(HttpClient);
  private destroyRef = inject(DestroyRef);
  @Input() requestHeaders: Record<string, string> = {};
  @Input() canEdit = false;
  @Input() refreshVersion = 0;
  @Output() sessionExpired = new EventEmitter<void>();
  @Output() savingChange = new EventEmitter<boolean>();
  result = signal<SettingsResult | null>(null);
  loading = signal(false);
  saving = signal(false);
  error = signal('');
  notice = signal('');
  dirty = false;
  private revision = 0;
  private requestVersion = 0;
  values: SamplingValues = {detection_fps: 5, face_analysis_interval: 0.5, face_rois_per_frame: 4, face_match_threshold: 0.75, detection_confidence: 0.1, person_detection_enabled: true, video_face_detection_threshold: 0.5, video_face_min_size: 8};
  private timer = window.setInterval(() => {if (this.result() && !this.result()!.applied && !this.saving() && !this.loading()) this.load(false);}, 2000);

  ngOnChanges(changes: SimpleChanges) {if (changes['refreshVersion'] || changes['canEdit']) this.load(true);}
  ngOnDestroy() {window.clearInterval(this.timer);}
  changed() {this.dirty = true; this.notice.set('');}
  get videoSizeValid() {return Number.isInteger(this.values.video_face_min_size);}
  load(overwrite = true) {
    if (this.saving()) return;
    const version = ++this.requestVersion;
    this.loading.set(true); this.error.set('');
    this.http.get<SettingsResult>('/api/function-settings').pipe(timeout(12000), takeUntilDestroyed(this.destroyRef)).subscribe({
      next: result => {
        if (version !== this.requestVersion) return;
        this.result.set(result); this.loading.set(false);
        if (overwrite || !this.dirty) {this.values = {...result.values}; this.revision = result.revision; this.dirty = false;}
      }, error: error => {if (version === this.requestVersion) {this.loading.set(false); this.failed(error);}}
    });
  }
  save(form: NgForm) {
    if (!form.valid || !this.videoSizeValid || !this.result() || !this.canEdit || this.saving() || this.loading()) return;
    ++this.requestVersion;
    this.saving.set(true); this.savingChange.emit(true); this.error.set(''); this.notice.set('');
    this.http.put<SettingsResult>('/api/function-settings', {...this.values, revision: this.revision}, {headers: this.requestHeaders})
      .pipe(timeout(20000), takeUntilDestroyed(this.destroyRef)).subscribe({
        next: result => {
          this.result.set(result); this.values = {...result.values}; this.revision = result.revision; this.dirty = false;
          this.saving.set(false); this.savingChange.emit(false);
          this.notice.set(result.applied ? '설정을 저장하고 실행 중인 분석에 반영했습니다.' : '설정을 저장했습니다. 분석 서비스에 반영될 때까지 상태를 확인합니다.');
        }, error: error => {this.saving.set(false); this.savingChange.emit(false); this.failed(error);}
      });
  }
  private failed(error: HttpErrorResponse) {
    if (error.status === 401) this.sessionExpired.emit();
    this.error.set(error.status === 409 ? '다른 화면에서 설정이 변경되었습니다. 저장값을 다시 불러온 후 수정해 주세요.' : error.status === 403 ? '설정을 변경할 권한이 없습니다.' : '설정을 확인하지 못했습니다. 서버 연결과 입력값을 확인해 주세요.');
  }
}
