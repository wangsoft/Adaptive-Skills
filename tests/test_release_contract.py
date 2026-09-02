from __future__ import annotations

import copy
import unittest
from pathlib import Path

import yaml

from scripts import check_release_contract as contract


class ReleaseWorkflowContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        workflow_path = Path(__file__).parents[1] / ".github" / "workflows" / "ci.yml"
        cls.workflow = yaml.load(
            workflow_path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader
        )

    def test_accepts_the_native_verified_release_data_flow(self) -> None:
        contract.validate_workflow(copy.deepcopy(self.workflow), contract.RELEASE_VERSION)

    def test_rejects_broadened_release_gates(self) -> None:
        workflow = copy.deepcopy(self.workflow)
        workflow["jobs"]["bundle"]["if"] = "always() || startsWith(github.ref, 'refs/tags/v')"
        with self.assertRaisesRegex(RuntimeError, "bundle gate"):
            contract.validate_workflow(workflow, contract.RELEASE_VERSION)

    def test_rejects_unpinned_or_reordered_artifact_steps(self) -> None:
        action_locations = [
            ("quality", "actions/checkout", "actions/checkout@v6"),
            ("bundle", "actions/setup-python", "actions/setup-python@v6"),
            ("bundle", "actions/setup-node", "actions/setup-node@v6"),
            ("bundle", "actions/upload-artifact", "actions/upload-artifact@v4"),
            ("release", "actions/checkout", "actions/checkout@v6"),
            ("release", "actions/download-artifact", "actions/download-artifact@v4"),
        ]
        for job_name, prefix, replacement in action_locations:
            with self.subTest(job=job_name, action=prefix):
                workflow = copy.deepcopy(self.workflow)
                step = next(
                    item
                    for item in workflow["jobs"][job_name]["steps"]
                    if str(item.get("uses", "")).startswith(prefix + "@")
                )
                step["uses"] = replacement
                with self.assertRaisesRegex(RuntimeError, "approved immutable action"):
                    contract.validate_workflow(workflow, contract.RELEASE_VERSION)

        workflow = copy.deepcopy(self.workflow)
        checkout = next(
            item
            for item in workflow["jobs"]["release"]["steps"]
            if str(item.get("uses", "")).startswith("actions/checkout@")
        )
        checkout["with"]["persist-credentials"] = "true"
        with self.assertRaisesRegex(RuntimeError, "persist credentials"):
            contract.validate_workflow(workflow, contract.RELEASE_VERSION)

        workflow = copy.deepcopy(self.workflow)
        bundle_steps = workflow["jobs"]["bundle"]["steps"]
        verify_index = next(
            index for index, step in enumerate(bundle_steps)
            if step.get("name") == "Verify packaged core"
        )
        upload_index = next(
            index for index, step in enumerate(bundle_steps)
            if step.get("name") == "Stage verified packages"
        )
        bundle_steps[verify_index], bundle_steps[upload_index] = (
            bundle_steps[upload_index],
            bundle_steps[verify_index],
        )
        with self.assertRaisesRegex(RuntimeError, "step order"):
            contract.validate_workflow(workflow, contract.RELEASE_VERSION)

    def test_rejects_merged_inputs_or_missing_repository_context(self) -> None:
        workflow = copy.deepcopy(self.workflow)
        release_steps = workflow["jobs"]["release"]["steps"]
        download = next(
            step for step in release_steps
            if step.get("name") == "Download verified platform packages"
        )
        download["with"]["merge-multiple"] = "true"
        with self.assertRaisesRegex(RuntimeError, "merge-multiple"):
            contract.validate_workflow(workflow, contract.RELEASE_VERSION)

        workflow = copy.deepcopy(self.workflow)
        publish = next(
            step for step in workflow["jobs"]["release"]["steps"]
            if step.get("name") == "Publish one release after every platform passes"
        )
        del publish["env"]["GH_REPO"]
        with self.assertRaisesRegex(RuntimeError, "repository context"):
            contract.validate_workflow(workflow, contract.RELEASE_VERSION)

    def test_rejects_a_release_without_exact_tag_version_check(self) -> None:
        workflow = copy.deepcopy(self.workflow)
        publish = next(
            step for step in workflow["jobs"]["release"]["steps"]
            if step.get("name") == "Publish one release after every platform passes"
        )
        publish["run"] = publish["run"].replace(
            'test "$GITHUB_REF_NAME" = "v${RELEASE_VERSION}"\n', ""
        )
        with self.assertRaisesRegex(RuntimeError, "tag version"):
            contract.validate_workflow(workflow, contract.RELEASE_VERSION)

    def test_rejects_release_reuse_or_non_draft_publication(self) -> None:
        workflow = copy.deepcopy(self.workflow)
        publish = next(
            step for step in workflow["jobs"]["release"]["steps"]
            if step.get("name") == "Publish one release after every platform passes"
        )
        publish["run"] = publish["run"].replace(
            'echo "Refusing to replace published release $tag" >&2\n', ""
        )
        with self.assertRaisesRegex(RuntimeError, "exact asset publication"):
            contract.validate_workflow(workflow, contract.RELEASE_VERSION)

        workflow = copy.deepcopy(self.workflow)
        publish = next(
            step for step in workflow["jobs"]["release"]["steps"]
            if step.get("name") == "Publish one release after every platform passes"
        )
        publish["run"] = publish["run"].replace(" --draft", "")
        with self.assertRaisesRegex(RuntimeError, "exact asset publication"):
            contract.validate_workflow(workflow, contract.RELEASE_VERSION)


if __name__ == "__main__":
    unittest.main()
