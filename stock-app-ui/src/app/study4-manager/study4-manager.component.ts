import { CommonModule } from '@angular/common';
import { ChangeDetectionStrategy, Component, OnInit, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { take } from 'rxjs';
import { DrawerComponent } from '../shared/ui/drawer.component';
import { ModalComponent } from '../shared/ui/modal.component';
import { Study4ExecutionService } from '../api/study4-execution.service';
import {
  Study4Audit, Study4CycleDetail, Study4Performance, Study4PlanItem, Study4ScanRow, Study4SetupRequirement, Study4Status
} from '../model/study4-execution';

@Component({
  selector: 'app-study4-manager',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink, DrawerComponent, ModalComponent],
  templateUrl: './study4-manager.component.html',
  styleUrl: './study4-manager.component.css',
  changeDetection: ChangeDetectionStrategy.Default
})
export class Study4ManagerComponent implements OnInit {
  private readonly api = inject(Study4ExecutionService);
  readonly killSwitchHelp = 'Kill switch stops new broker submissions immediately. It does not sell or flatten positions.';

  status: Study4Status | null = null;
  detail: Study4CycleDetail | null = null;
  performance: Study4Performance | null = null;
  audit: Study4Audit | null = null;
  loading = true;
  error = '';
  tab: 'overview' | 'tracking' | 'plan' | 'execute' | 'reconcile' | 'performance' | 'audit' = 'overview';
  selectedRow: Study4ScanRow | null = null;
  confirmOpen = false;
  confirmToken = '';
  productionAck = '';
  resetOpen = false;
  resetAck = '';
  busy = false;
  message = '';
  trackingSession = '';

  ngOnInit(): void {
    this.refresh();
  }

  refresh(): void {
    this.loading = true;
    this.error = '';
    this.api.status().pipe(take(1)).subscribe({
      next: status => {
        this.status = status;
        this.loading = false;
        const week = status.cycle?.iso_week;
        if (week) {
          this.api.cycle(week).pipe(take(1)).subscribe(detail => {
            this.detail = detail;
            const snaps = detail.snapshots ?? [];
            this.trackingSession = snaps[snaps.length - 1]?.session || '';
          });
        }
        this.api.performance().pipe(take(1)).subscribe(series => { this.performance = series; });
        this.api.audit().pipe(take(1)).subscribe(events => { this.audit = events; });
      },
      error: () => {
        this.loading = false;
        this.error = 'Study 4 execution status is unavailable.';
      }
    });
  }

  environmentBanner(): string {
    if (!this.status?.configured) return 'NOT CONFIGURED';
    return (this.status.environment || 'SANDBOX').toUpperCase();
  }

  environmentClass(): string {
    if (this.status?.environment === 'production') return 'banner-neg';
    return 'banner-warn';
  }

  pendingSetup(): Study4SetupRequirement[] {
    return (this.status?.setupRequirements ?? []).filter(item => !item.ready);
  }

  setupComplete(): boolean {
    return this.status?.configured === true && this.pendingSetup().length === 0;
  }

  lifecycleLabel(): string {
    if (!this.status) return '—';
    if (this.pendingSetup().length) return 'Not configured';
    if (!this.status.cycle) return 'Ready for first scan';
    return this.status.cycle.state;
  }

  nextActionLabel(): string {
    const action = this.status?.nextAction;
    const labels: Record<string, string> = {
      configure_execution_mode: 'Complete setup below',
      scan: 'Run EOD scan',
      scan_or_wait_for_cutoff: 'Scan or wait for cutoff',
      preflight: 'Run preflight',
      confirm: 'Confirm the frozen plan',
      wait_for_open_or_execute: 'Wait for the scheduled open',
      monitor: 'Monitor broker orders',
      reconcile: 'Reconcile broker state',
      final_sync: 'Run final sync',
      await_next_signal_session: 'Wait for next signal session',
      review: 'Review the blocked cycle',
      review_missed_batch: 'Review the missed batch'
    };
    return action ? labels[action] ?? action.replaceAll('_', ' ') : '—';
  }

  scanRows(): Study4ScanRow[] {
    const snaps = this.detail?.snapshots ?? [];
    const latest = snaps.find(item => item.session === this.trackingSession) ?? snaps[snaps.length - 1];
    return latest?.payload?.scanRows ?? [];
  }

  snapshotSessions(): string[] {
    return (this.detail?.snapshots ?? []).map(item => item.session);
  }

  scanChanges(): { added: string[]; retained: string[]; dropped: string[] } {
    const snaps = this.detail?.snapshots ?? [];
    if (snaps.length < 2) {
      return { added: this.scanRows().map(row => row.ticker), retained: [], dropped: [] };
    }
    const current = snaps[snaps.length - 1]?.payload?.scanRows ?? [];
    const prior = snaps[snaps.length - 2]?.payload?.scanRows ?? [];
    const currentIds = new Set(current.map(row => row.ticker));
    const priorIds = new Set(prior.map(row => row.ticker));
    return {
      added: [...currentIds].filter(id => !priorIds.has(id)),
      retained: [...currentIds].filter(id => priorIds.has(id)),
      dropped: [...priorIds].filter(id => !currentIds.has(id))
    };
  }

  heldRows(): unknown[] {
    const snaps = this.detail?.snapshots ?? [];
    const latest = snaps.find(item => item.session === this.trackingSession) ?? snaps[snaps.length - 1];
    return latest?.payload?.positionDecisions ?? [];
  }

  planItems(): Study4PlanItem[] {
    return this.detail?.plan?.payload?.planItems
      ?? this.detail?.plan?.items?.map(item => item.payload)
      ?? [];
  }

  itemsOf(kind: string): Study4PlanItem[] {
    return this.planItems().filter(item => item.kind === kind);
  }

  run(action: 'scan' | 'finalize' | 'preflight' | 'reconcile' | 'finalSync'): void {
    this.busy = true;
    this.message = '';
    const done = () => { this.busy = false; this.refresh(); };
    const fail = (err: { error?: { detail?: { message?: string } | string } }) => {
      this.busy = false;
      const detail = err.error?.detail;
      this.error = typeof detail === 'string' ? detail : detail?.message || 'The action was refused.';
    };
    if (action === 'scan') this.api.scan().subscribe({ next: done, error: fail });
    if (action === 'finalize') this.api.finalize().subscribe({ next: done, error: fail });
    if (action === 'preflight') {
      this.api.preflight().subscribe({
        next: result => {
          this.confirmToken = result.confirmationChallenge;
          this.confirmOpen = true;
          this.busy = false;
        },
        error: fail
      });
    }
    if (action === 'reconcile') this.api.reconcile().subscribe({ next: done, error: fail });
    if (action === 'finalSync') this.api.finalSync().subscribe({ next: done, error: fail });
  }

  toggleKillSwitch(): void {
    const next = !this.status?.killSwitch;
    this.busy = true;
    this.message = '';
    this.api.killSwitch(next).subscribe({
      next: () => {
        this.busy = false;
        this.message = next
          ? 'Kill switch is on. New submissions are blocked.'
          : 'Kill switch released. Submissions follow the usual gates.';
        this.refresh();
      },
      error: err => {
        this.busy = false;
        this.error = err.error?.detail?.message || 'Kill switch update failed.';
      }
    });
  }

  initializeCapital(): void {
    const target = this.status?.configuredTargetBucket;
    if (target == null) return;
    this.busy = true;
    this.api.capitalReset(target).subscribe({
      next: () => {
        this.busy = false;
        this.message = 'Capital version recorded from the configured target.';
        this.refresh();
      },
      error: err => {
        this.busy = false;
        this.error = err.error?.detail?.message || 'Capital initialization failed.';
      }
    });
  }

  confirmReset(): void {
    this.busy = true;
    this.api.resetLedger(this.resetAck).subscribe({
      next: () => {
        this.resetOpen = false;
        this.resetAck = '';
        this.busy = false;
        this.message = 'Local execution stats were wiped. Broker positions are unchanged.';
        this.detail = null;
        this.performance = null;
        this.audit = null;
        this.refresh();
      },
      error: err => {
        this.busy = false;
        this.error = err.error?.detail?.message || err.error?.detail || 'Reset failed.';
      }
    });
  }

  confirmPlan(): void {
    this.busy = true;
    this.api.confirm(
      this.confirmToken,
      this.status?.environment === 'production' ? this.productionAck : undefined
    ).subscribe({
      next: () => {
        this.confirmOpen = false;
        this.busy = false;
        this.message = 'The immutable batch is armed. It will submit only at the scheduled open.';
        this.refresh();
      },
      error: err => {
        this.busy = false;
        this.error = err.error?.detail?.message || 'Confirmation failed.';
      }
    });
  }

  executeArmed(): void {
    const planId = (this.detail?.plan as { id?: number } | null)?.id;
    this.busy = true;
    this.api.execute(planId).subscribe({
      next: () => { this.busy = false; this.refresh(); },
      error: err => {
        this.busy = false;
        this.error = err.error?.detail?.message || 'Execute refused.';
      }
    });
  }

  reconcileUnknown(): void {
    this.run('reconcile');
  }
}
