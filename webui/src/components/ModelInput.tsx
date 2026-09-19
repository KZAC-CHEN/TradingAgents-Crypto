import { forwardRef, useId, type InputHTMLAttributes } from "react";

import type { ModelInfo } from "../types";

interface ModelInputProps extends InputHTMLAttributes<HTMLInputElement> {
  models?: ModelInfo[];
}

export const ModelInput = forwardRef<HTMLInputElement, ModelInputProps>(
  function ModelInput({ models = [], ...props }, ref) {
    const listId = useId();
    return (
      <>
        <input ref={ref} list={models.length ? listId : undefined} autoComplete="off" {...props} />
        {models.length ? (
          <datalist id={listId}>
            {models.map((model) => (
              <option key={model.id} value={model.id}>{model.label}</option>
            ))}
          </datalist>
        ) : null}
      </>
    );
  },
);
