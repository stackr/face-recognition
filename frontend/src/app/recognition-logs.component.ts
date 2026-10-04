import {CommonModule} from '@angular/common';
import {HttpClient, HttpErrorResponse, HttpParams} from '@angular/common/http';
import {Component, DestroyRef, EventEmitter, Input, OnChanges, OnDestroy, Output, inject, signal} from '@angular/core';
import {takeUntilDestroyed} from '@angular/core/rxjs-interop';
import {FormsModule} from '@angular/forms';
import {timeout} from 'rxjs';
import {recognitionOutcomes, recognitionReasons} from './recognition-labels';

interface LogItem {
  id: number; camera_id: number; camera_name: string; stream_session_id: string; track_id: number; frame_id: number;
  captured_at: string; outcome: string; reasons: string[]; quality: number; top_similarity: number | null; sample_count: number; settings_revision: number;
  metrics: {face_size?: number[]; blur_score?: number; brightness?: number; yaw?: number; pitch?: number; roll?: number; threshold?: number; supporting_samples?: number; minimum_samples?: number; detector_region?: string; detector_input?: number; detection_passes?: number};
}
interface LogPage {items: LogItem[]; has_more: boolean; next_cursor: number | null; summary: {total: number; outcomes: Record<string, number>; reasons: Record<string, number>}; retention_days: number; max_records: number;}

@Component({selector: 'app-recognition-logs', standalone: true, imports: [CommonModule, FormsModule], templateUrl: './recognition-logs.component.html'})
export class RecognitionLogsComponent implements OnChanges, OnDestroy {
  private http = inject(HttpClient);
  private destroyRef = inject(DestroyRef);
  @Input() cameras: {camera_id: number; name: string; can_view: boolean}[] = [];
  @Input() requestHeaders: Record<string, string> = {};
  @Input() canDelete = false;
  @Input() refreshVersion = 0;
  @Output() sessionExpired = new EventEmitter<void>();
  page = signal<LogPage | null>(null);
  loading = signal(false);
  error = signal('');
  notice = signal('');
  deleting = signal(false);
  autoRefresh = true;
  filters = {camera_id: '', outcome: '', reason: '', start: '', end: ''};
  private appliedFilters = {...this.filters};
  outcomes = Object.entries(recognitionOutcomes);
  reasons = Object.entries(recognitionReasons);
  private cursors: (number | undefined)[] = [undefined];
  pageNumber = 1;
  private version = 0;
  private timer = window.setInterval(() => {if (this.autoRefresh && this.pageNumber === 1 && !this.loading()) this.load();}, 5000);
  ngOnChanges(changes: Record<string, unknown>) {if (changes['refreshVersion']) this.load();}
  ngOnDestroy() {window.clearInterval(this.timer);}
  applyFilters() {
    if (this.deleting()) return;
    if (this.filters.start && this.filters.end && new Date(this.filters.start) >= new Date(this.filters.end)) {this.error.set('종료 시각은 시작 시각보다 뒤여야 합니다.'); return;}
    this.appliedFilters = {...this.filters}; this.cursors = [undefined]; this.pageNumber = 1; this.load();
  }
  reset() {this.filters = {camera_id: '', outcome: '', reason: '', start: '', end: ''}; this.applyFilters();}
  latest() {this.cursors = [undefined]; this.pageNumber = 1; this.load();}
  next() {const cursor = this.page()?.next_cursor; if (!cursor || this.loading()) return; this.cursors.push(cursor); this.pageNumber++; this.load();}
  previous() {if (this.pageNumber <= 1 || this.loading()) return; this.cursors.pop(); this.pageNumber--; this.load();}
  load() {
    if (this.deleting()) return;
    const version = ++this.version;
    this.loading.set(true); this.error.set('');
    let params = new HttpParams().set('limit', 50);
    for (const [key, value] of Object.entries(this.appliedFilters)) if (value) params = params.set(key, key === 'start' || key === 'end' ? new Date(value).toISOString() : value);
    const cursor = this.cursors.at(-1); if (cursor) params = params.set('before_id', cursor);
    this.http.get<LogPage>('/api/recognition-logs', {params}).pipe(timeout(12000), takeUntilDestroyed(this.destroyRef)).subscribe({
      next: page => {if (version === this.version) {this.page.set(page); this.loading.set(false);}},
      error: (error: HttpErrorResponse) => {
        if (version !== this.version) return;
        this.loading.set(false); this.page.set(null);
        if (error.status === 401) this.sessionExpired.emit();
        this.error.set(error.status === 403 ? '이 카메라의 로그를 조회할 권한이 없습니다.' : '로그를 불러오지 못했습니다. 서버 연결과 검색 조건을 확인해 주세요.');
      }
    });
  }
  deleteAll() {
    if (!this.canDelete || this.loading() || this.deleting()
      || !window.confirm('검색 조건과 관계없이 모든 카메라의 전체 얼굴 검사 로그를 삭제할까요? 삭제 후 복구할 수 없습니다.')) return;
    ++this.version;
    this.deleting.set(true); this.error.set(''); this.notice.set('');
    this.http.delete<{deleted_count: number}>('/api/recognition-logs', {headers:this.requestHeaders}).pipe(timeout(12000), takeUntilDestroyed(this.destroyRef)).subscribe({
      next: result => {
        this.deleting.set(false); this.page.set(null);
        this.notice.set(`전체 로그 ${result.deleted_count.toLocaleString()}건을 삭제했습니다.`);
        this.latest();
      },
      error: (error: HttpErrorResponse) => {
        this.deleting.set(false);
        if (error.status === 401) this.sessionExpired.emit();
        this.error.set(error.status === 403 ? '전체 로그를 삭제할 권한이 없습니다.' : '로그 삭제 결과를 확인하지 못했습니다. 최신 기록을 조회해 주세요.');
      }
    });
  }
  outcomeLabel(value: string) {return recognitionOutcomes[value] || value;}
  reasonLabel(value: string) {return recognitionReasons[value] || value;}
  reasonSummary() {return Object.entries(this.page()?.summary.reasons || {}).sort((a,b) => b[1] - a[1]);}
}
