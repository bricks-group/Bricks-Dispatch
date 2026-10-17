import type { ButtonHTMLAttributes, ReactNode } from 'react';
import { Link, type LinkProps } from 'react-router-dom';

/**
 * The one button for Dispatch web. 44px tall, radius 2, one-line labels.
 * primary: solid off-white · secondary: 1px graphite outline · tertiary: text only · danger: red outline.
 */
export type ButtonVariant = 'primary' | 'secondary' | 'tertiary' | 'danger';

export function btnClass(variant: ButtonVariant = 'secondary', extra?: string) {
  return ['btn', `btn-${variant}`, extra].filter(Boolean).join(' ');
}

type Props = ButtonHTMLAttributes<HTMLButtonElement> & { variant?: ButtonVariant; icon?: ReactNode };

export function Button({ variant = 'secondary', icon, className, children, type = 'button', ...rest }: Props) {
  return (
    <button type={type} className={btnClass(variant, className)} {...rest}>
      {icon}
      <span className="btn-label">{children}</span>
    </button>
  );
}

export function ButtonLink({ variant = 'secondary', className, children, ...rest }: LinkProps & { variant?: ButtonVariant }) {
  return (
    <Link className={btnClass(variant, className)} {...rest}>
      <span className="btn-label">{children}</span>
    </Link>
  );
}
