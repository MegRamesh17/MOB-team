-- 031_create_pet_purchases.sql
--
-- The floating pet's shop. One row per item an employee owns; `equipped` marks which
-- owned item (at most one per catalog slot -- head/eyes/neck/back -- enforced in code by
-- api/function_app.py's pet_equip, the same way scripts/devserver.py's Bank.pet_equip
-- does it locally) is currently worn.
--
-- No points_balance column here on purpose: points are derived on read from
-- COUNT(DISTINCT doc_title) on dbo.Certificates (api/shared/pet_shop.py) rather than
-- stored, the same reasoning docs/q-score.md gives for not storing Q Score -- a stored
-- balance would need debiting and crediting in lockstep with certificates issued and
-- items bought everywhere either happens, and would go stale the moment one of those
-- call sites was missed.

IF OBJECT_ID('dbo.PetPurchases', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.PetPurchases
    (
        id            INT IDENTITY(1,1) NOT NULL CONSTRAINT PK_PetPurchases PRIMARY KEY,
        employee_id   INT           NOT NULL,
        company_id    INT           NOT NULL,
        item_id       VARCHAR(40)   NOT NULL,
        equipped      BIT           NOT NULL DEFAULT 0,
        purchased_at  DATETIME2(3)  NOT NULL DEFAULT SYSUTCDATETIME(),

        CONSTRAINT UQ_PetPurchases_Employee_Item UNIQUE (employee_id, item_id),
        CONSTRAINT FK_PetPurchases_Employee FOREIGN KEY (employee_id)
            REFERENCES dbo.Employees(id),
        CONSTRAINT FK_PetPurchases_Company FOREIGN KEY (company_id)
            REFERENCES dbo.Companies(id)
    );

    CREATE INDEX IX_PetPurchases_Employee ON dbo.PetPurchases(employee_id);
END
GO
