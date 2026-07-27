import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';
import { StudyCatalog, StudyDetail } from '../studies/study.models';

@Injectable({ providedIn: 'root' })
export class StudiesService {
  private readonly http = inject(HttpClient);
  private readonly apiBaseUrl = window.location.port === '4200'
    ? 'http://localhost:8000'
    : window.location.origin;
  private readonly studiesUrl = `${this.apiBaseUrl}/api/studies`;

  getCatalog(): Observable<StudyCatalog> {
    return this.http.get<StudyCatalog>(this.studiesUrl);
  }

  getStudy(studyId: string): Observable<StudyDetail> {
    return this.http.get<StudyDetail>(`${this.studiesUrl}/${encodeURIComponent(studyId)}`);
  }

  runScan(studyId: string): Observable<{ status: string; message?: string; output?: string; durationMs?: number }> {
    return this.http.post<{ status: string; message?: string; output?: string; durationMs?: number }>(
      `${this.studiesUrl}/${encodeURIComponent(studyId)}/scan`, {});
  }

  getScan(studyId: string): Observable<{ generatedAt: string; candidates: any[] }> {
    return this.http.get<{ generatedAt: string; candidates: any[] }>(
      `${this.studiesUrl}/${encodeURIComponent(studyId)}/scan`);
  }
}
