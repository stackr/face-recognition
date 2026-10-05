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
    if (this.filters.start && this.filters.end && new Date(this.filters.start) >= new Date(this.filters.end)) {this.error.set('End time must be later than start time.'); return;}
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
        this.error.set(error.status === 403 ? 'You do not have permission to view this camera logs.' : 'Could not load logs. Check the server connection and filters.');
      }
    });
  }
  deleteAll() {
    if (!this.canDelete || this.loading() || this.deleting()
      || !window.confirm('Delete all face analysis logs for every camera, regardless of the current filters? This cannot be undone.')) return;
    ++this.version;
    this.deleting.set(true); this.error.set(''); this.notice.set('');
    this.http.delete<{deleted_count: number}>('/api/recognition-logs', {headers:this.requestHeaders}).pipe(timeout(12000), takeUntilDestroyed(this.destroyRef)).subscribe({
      next: result => {
        this.deleting.set(false); this.page.set(null);
        this.notice.set(`Deleted ${result.deleted_count.toLocaleString("en-US")} log records.`);
        this.latest();
      },
      error: (error: HttpErrorResponse) => {
        this.deleting.set(false);
        if (error.status === 401) this.sessionExpired.emit();
        this.error.set(error.status === 403 ? 'You do not have permission to delete all logs.' : 'Could not confirm log deletion. Reload the latest records.');
      }
    });
  }
  outcomeLabel(value: string) {return recognitionOutcomes[value] || value;}
  reasonLabel(value: string) {return recognitionReasons[value] || value;}
  reasonSummary() {return Object.entries(this.page()?.summary.reasons || {}).sort((a,b) => b[1] - a[1]);}
}
