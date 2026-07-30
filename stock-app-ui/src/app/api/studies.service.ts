import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';
import { JobCommandResult } from '../model/job-results';
import { StudyCatalog, StudyDetail, StudyScanSnapshot } from '../studies/study.models';
import { API_BASE_URL } from './api-base';

@Injectable({ providedIn: 'root' })
export class StudiesService {
  private readonly http = inject(HttpClient);
  private readonly studiesUrl = `${inject(API_BASE_URL)}/api/studies`;

  getCatalog(): Observable<StudyCatalog> {
    return this.http.get<StudyCatalog>(this.studiesUrl);
  }

  getStudy(studyId: string): Observable<StudyDetail> {
    return this.http.get<StudyDetail>(`${this.studiesUrl}/${encodeURIComponent(studyId)}`);
  }

  runScan(studyId: string): Observable<JobCommandResult> {
    return this.http.post<JobCommandResult>(
      `${this.studiesUrl}/${encodeURIComponent(studyId)}/scan`, {});
  }

  getScan(studyId: string): Observable<StudyScanSnapshot> {
    return this.http.get<StudyScanSnapshot>(
      `${this.studiesUrl}/${encodeURIComponent(studyId)}/scan`);
  }
}
