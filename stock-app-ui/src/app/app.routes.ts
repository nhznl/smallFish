import { ActivatedRouteSnapshot, Routes } from '@angular/router';
import { MomentumScannerComponent } from './momentum-scanner/momentum-scanner.component';
import { StockDetailComponent } from './stock-detail/stock-detail.component';
import { StudiesComponent } from './studies/studies.component';
import { WheelComponent } from './wheel/wheel.component';
import { RetirementPortfolioComponent } from './retirement-portfolio/retirement-portfolio.component';
import { PageNotFoundComponent } from './page-not-found/page-not-found.component';
import { OptionsComponent } from './options/options.component';

/** Stock detail is the one route whose title depends on the URL. */
const stockDetailTitle = (route: ActivatedRouteSnapshot) => {
  const symbol = route.paramMap.get('symbol');
  return symbol ? `${symbol.toUpperCase()} · smallFish` : 'Stock Detail · smallFish';
};

export const routes: Routes = [
  { path: 'momentum', component: MomentumScannerComponent, title: 'Momentum · smallFish' },
  { path: 'studies', component: StudiesComponent, title: 'Research Studies · smallFish' },
  { path: 'studies/:studyId', component: StudiesComponent, title: 'Research Studies · smallFish' },
  { path: 'sectors', title: 'Sectors · smallFish', loadComponent: () => import('./sector-rotation/sector-rotation.component').then(m => m.SectorRotationComponent) },
  { path: 'wheel', component: WheelComponent, title: 'Wheel · smallFish' },
  { path: 'wheelExplainer', title: 'Wheel Explainer · smallFish', loadComponent: () => import('./wheel-explainer/wheel-explainer.component').then(m => m.WheelExplainerComponent) },
  { path: 'options', component: OptionsComponent, title: 'Options Ledger · smallFish' },
  { path: 'retirement', component: RetirementPortfolioComponent, title: 'Retirement Ledger · smallFish' },
  { path: 'portfolios', title: 'Portfolios · smallFish', loadComponent: () => import('./portfolios/portfolios.component').then(m => m.PortfoliosComponent) },
  { path: 'stockDetail/:symbol', component: StockDetailComponent, title: stockDetailTitle },
  { path: '', redirectTo: '/momentum', pathMatch: 'full' },
  { path: '**', component: PageNotFoundComponent, title: 'Not Found · smallFish' }
];
