import { ChangeDetectionStrategy, Component } from '@angular/core';
import { RouterLink } from '@angular/router';

@Component({
  selector: 'app-pre-earnings-explainer',
  standalone: true,
  imports: [RouterLink],
  templateUrl: './pre-earnings-explainer.component.html',
  styleUrl: './pre-earnings-explainer.component.css',
  changeDetection: ChangeDetectionStrategy.Eager
})
export class PreEarningsExplainerComponent {}
