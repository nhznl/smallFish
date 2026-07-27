import { Injectable } from '@angular/core';
import { BehaviorSubject } from 'rxjs';

@Injectable({
  providedIn: 'root'
})
export class SymbolFilterService {
  private filterSubject = new BehaviorSubject<string>('');
  public filter$ = this.filterSubject.asObservable();

  constructor() { }

  setFilter(filter: string): void {
    this.filterSubject.next(filter);
  }

  getFilter(): string {
    return this.filterSubject.value;
  }

  clearFilter(): void {
    this.filterSubject.next('');
  }

  getFilteredSymbols(): string[] {
    const filter = this.getFilter().trim();
    if (!filter) {
      return [];
    }
    
    return filter
      .split(/[\s,]+/)
      .map(symbol => symbol.trim().toUpperCase())
      .filter(symbol => symbol.length > 0);
  }

  matchesFilter(symbol: string): boolean {
    const filteredSymbols = this.getFilteredSymbols();
    if (filteredSymbols.length === 0) {
      return true; // No filter applied, show all
    }
    
    return filteredSymbols.includes(symbol.toUpperCase());
  }
}
